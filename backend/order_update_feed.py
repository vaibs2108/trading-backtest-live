"""
order_update_feed.py — Fast-path wake-up for order fill confirmation via
Dhan's Order Update WebSocket (wss://api-order-update.dhan.co).

Mirrors live_feed.py's architecture: a dedicated daemon background thread
running its own reconnect-with-fixed-delay loop, not asyncio-native (the
dhanhq SDK's OrderUpdate class ships a connect_to_dhan_websocket_sync()
method built exactly for this, matching MarketFeed's own thread-based
usage in live_feed.py).

DESIGN NOTE — why this is a wake-up trigger, not a status source:
Dhan's Order Update WebSocket payload schema (field name "status" on the
pushed Data dict) is not documented/confirmed against the same enum
values the REST get_order_by_id endpoint uses ("orderStatus": TRADED /
REJECTED / CANCELLED / etc, which broker.py's verify_order_fill() already
parses correctly and is the one alignment point with Dhan's confirmed
docs). Rather than trust an unconfirmed WS field for a decision that
gates real order execution, this module only uses the WS message as an
EARLY WAKE-UP SIGNAL: when an update arrives for an order_id someone is
waiting on, wait_for_update() returns immediately instead of waiting out
its poll interval, letting the caller do its own authoritative REST
check sooner. The REST check itself is unchanged and remains the single
source of truth for fill status.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_running = False
_thread = None

_lock = threading.Lock()
_order_events = {}  # order_id (str) -> threading.Event


def register(order_id: str):
    """Call right after an order_id is known (order placed), before
    waiting on it, so no update can be missed between placement and the
    first wait_for_update() call."""
    order_id = str(order_id)
    with _lock:
        if order_id not in _order_events:
            _order_events[order_id] = threading.Event()


def wait_for_update(order_id: str, timeout: float = 0.5) -> bool:
    """Block until an order-update message arrives for this order_id, or
    timeout elapses. Returns True if a message arrived (caller should
    re-check REST status immediately), False on timeout (caller falls
    through to its normal poll cadence). Never raises."""
    order_id = str(order_id)
    with _lock:
        ev = _order_events.get(order_id)
        if ev is None:
            ev = threading.Event()
            _order_events[order_id] = ev
    fired = ev.wait(timeout)
    if fired:
        ev.clear()  # allow a second wait (e.g. PART_TRADED then TRADED)
    return fired


def unregister(order_id: str):
    """Call once fill verification for this order is done, to avoid
    leaking Event objects for long-running sessions."""
    with _lock:
        _order_events.pop(str(order_id), None)


def _on_update(order_update: dict):
    try:
        data = order_update.get("Data", {}) if isinstance(order_update, dict) else {}
        order_id = str(data.get("orderNo") or "")
        if not order_id:
            return
        status = data.get("status", "")
        logger.debug(f"[OrderUpdateFeed] update for order {order_id}: status={status}")
        with _lock:
            ev = _order_events.get(order_id)
        if ev is not None:
            ev.set()
    except Exception as e:
        logger.debug(f"[OrderUpdateFeed] error handling update: {e}")


def _run(client_id: str, access_token: str):
    """Background thread: connect to the Order Update WebSocket and
    reconnect on drop, mirroring live_feed.py's fixed-5s-retry pattern."""
    global _running
    while _running:
        try:
            from dhanhq import DhanContext
            from dhanhq.orderupdate import OrderUpdate

            dhan_context = DhanContext(client_id, access_token)
            order_update = OrderUpdate(dhan_context)
            order_update.on_update = _on_update
            logger.info("OrderUpdateFeed connecting...")
            order_update.connect_to_dhan_websocket_sync()  # blocks until the session ends
        except Exception as e:
            logger.warning(f"OrderUpdateFeed connection error: {e}")

        if _running:
            logger.info("OrderUpdateFeed reconnecting in 5s...")
            time.sleep(5)


def start(client_id: str, access_token: str):
    """Start the Order Update WebSocket feed in a background thread.
    Safe to call even if fill confirmation would otherwise work fine via
    REST polling alone — this is purely a latency optimization, and
    verify_order_fill() falls back to its normal poll cadence if this
    feed is never started or is currently disconnected."""
    global _running, _thread
    if _running:
        logger.warning("OrderUpdateFeed already running")
        return
    _running = True
    _thread = threading.Thread(
        target=_run,
        args=(client_id, access_token),
        daemon=True,
        name="OrderUpdateFeed",
    )
    _thread.start()
    logger.info("OrderUpdateFeed started")


def stop():
    global _running
    _running = False
    logger.info("OrderUpdateFeed stopped")
