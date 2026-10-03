"""
auth.py — app login (single user), session cookie, lockout.

Credentials live in .env only (APP_USERNAME / APP_PASSWORD), never in settings.json and never
sent to the browser. A login sets an HttpOnly, SameSite=Strict cookie signed with
APP_SESSION_SECRET (also .env, generated on first start), so a backend restart doesn't log
you out. Sessions last SESSION_HOURS.

Lockout: LOCK_AFTER wrong passwords from one IP within LOCK_MINUTES -> that IP is refused
for LOCK_MINUTES. Kept in memory (a restart clears it).

Added 2026-10-03 (before this the app had no login, and it listens on the LAN).
"""
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

COOKIE = "algo_session"
SESSION_HOURS = 12
LOCK_AFTER = 5
LOCK_MINUTES = 15
DEFAULTS = {"APP_USERNAME": "admin", "APP_PASSWORD": "123456"}   # you change these in .env

_lock = threading.Lock()
_failures = {}      # ip -> [timestamps of recent failures]
_locked_until = {}  # ip -> epoch seconds


def _load_env() -> dict:
    """Load .env into the environment (never overriding values already set) and return the
    file's own values. Done here, not left to config.py, which only loads .env when settings
    are first read -- otherwise a missing key could look 'unset' at startup."""
    from config import BASE_DIR
    from dotenv import dotenv_values, load_dotenv
    path = BASE_DIR.parent / ".env"
    load_dotenv(dotenv_path=path)
    return dotenv_values(path) if path.exists() else {}


def ensure_env_defaults() -> None:
    """First start: add APP_USERNAME / APP_PASSWORD / APP_SESSION_SECRET to .env -- only keys
    that are missing from the FILE, so a password you changed is never overwritten."""
    from dhan_tokens import _write_env_line
    file_vals = _load_env()
    for key, val in DEFAULTS.items():
        if not (file_vals.get(key) or "").strip():
            _write_env_line(key, val)
            logger.info(f"auth: {key} was not set -- added the default to .env (change it there)")
    if not (file_vals.get("APP_SESSION_SECRET") or "").strip():
        _write_env_line("APP_SESSION_SECRET", secrets.token_hex(32))
        logger.info("auth: generated APP_SESSION_SECRET in .env")


_load_env()


def _secret() -> bytes:
    return os.environ.get("APP_SESSION_SECRET", "").encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(username: str) -> str:
    expires = int(time.time()) + SESSION_HOURS * 3600
    payload = f"{username}|{expires}"
    return f"{payload}|{_sign(payload)}"


def check_session(token: Optional[str]) -> Optional[str]:
    """Username if the cookie is valid and not expired, else None."""
    if not token or not _secret():
        return None
    try:
        username, expires, sig = token.rsplit("|", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(f"{username}|{expires}")):
        return None
    if int(expires) < time.time() or username != os.environ.get("APP_USERNAME"):
        return None
    return username


def locked_for(ip: str) -> int:
    """Seconds left on this IP's lockout (0 = not locked)."""
    with _lock:
        until = _locked_until.get(ip, 0)
    return max(0, int(until - time.time()))


def check_login(ip: str, username: str, password: str) -> Tuple[bool, str, bool]:
    """-> (ok, message, just_locked). Constant-time comparison; counts failures per IP."""
    left = locked_for(ip)
    if left:
        return False, f"Too many wrong attempts. Try again in {(left + 59) // 60} min.", False
    want_user = os.environ.get("APP_USERNAME", "")
    want_pass = os.environ.get("APP_PASSWORD", "")
    ok = bool(want_user) and bool(want_pass) and \
        hmac.compare_digest((username or "").encode(), want_user.encode()) & \
        hmac.compare_digest((password or "").encode(), want_pass.encode())
    now = time.time()
    with _lock:
        if ok:
            _failures.pop(ip, None)
            return True, "OK", False
        recent = [t for t in _failures.get(ip, []) if now - t < LOCK_MINUTES * 60] + [now]
        _failures[ip] = recent
        if len(recent) >= LOCK_AFTER:
            _locked_until[ip] = now + LOCK_MINUTES * 60
            _failures.pop(ip, None)
            return False, f"Too many wrong attempts. Locked for {LOCK_MINUTES} min.", True
        return False, f"Wrong username or password ({LOCK_AFTER - len(recent)} attempts left).", False
