"""
dhan_tokens.py — Dhan access tokens / client IDs and Telegram details, edited from the Settings page.

Dhan access tokens are JWTs that carry their own expiry ("exp") and client ID
("dhanClientId"), so the expiry can be read locally without calling Dhan. They last
~24 hours, so they have to be replaced often; this lets a user paste a new one in
Settings instead of editing .env and restarting.

Two independent tokens (same Dhan account, separate tokens):
  main — trading, live feed, order-update feed   (.env: DHAN_CLIENT_CODE / DHAN_TOKEN_ID)
  cas  — CAS scanner                             (.env: DHAN_CAS_CLIENT_CODE / DHAN_CAS_TOKEN_ID)

Tokens are never logged or returned to the browser — only a masked form.
"""
import base64
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from config import BASE_DIR, get_settings

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
WARN_MINUTES = 120          # banner from 2 hours before expiry
_ENV_PATH = BASE_DIR.parent / ".env"
_env_lock = threading.Lock()

SLOTS = {
    "main": {"label": "Trading", "client_env": "DHAN_CLIENT_CODE", "token_env": "DHAN_TOKEN_ID",
             "client_field": "dhan_client_code", "token_field": "dhan_access_token"},
    "cas": {"label": "CAS scanner", "client_env": "DHAN_CAS_CLIENT_CODE", "token_env": "DHAN_CAS_TOKEN_ID",
            "client_field": "dhan_cas_client_code", "token_field": "dhan_cas_access_token"},
}


def decode(token: str) -> Optional[dict]:
    """The token's claims (unverified — only used to read exp / dhanClientId)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def _mask(value: str) -> str:
    return ("••••" + value[-4:]) if value and len(value) > 4 else ("••••" if value else "")


def status(slot: str) -> dict:
    """Masked identity + expiry state of one token: ok | expiring | expired | missing | unreadable."""
    meta = SLOTS[slot]
    cfg = get_settings()
    client = (getattr(cfg, meta["client_field"], "") or "").strip()
    token = (getattr(cfg, meta["token_field"], "") or "").strip()
    out = {"slot": slot, "label": meta["label"], "client_id": _mask(client), "token": _mask(token),
           "expires_at": None, "minutes_left": None, "state": "missing"}
    if not token:
        return out
    claims = decode(token)
    if not claims or "exp" not in claims:
        out["state"] = "unreadable"
        return out
    exp = datetime.fromtimestamp(int(claims["exp"]), _IST)
    minutes_left = int((exp - datetime.now(_IST)).total_seconds() // 60)
    out.update(expires_at=exp.isoformat(), minutes_left=minutes_left,
               state="expired" if minutes_left <= 0 else ("expiring" if minutes_left <= WARN_MINUTES else "ok"))
    return out


def all_status() -> dict:
    return {slot: status(slot) for slot in SLOTS}


def check_new_token(slot: str, token: str, client_id: Optional[str] = None) -> tuple[bool, str]:
    """Validate a pasted token before saving: readable, not expired, belongs to the
    client ID (the configured one, or `client_id` when that is being changed), and
    accepted by Dhan's profile API."""
    meta = SLOTS[slot]
    token = (token or "").strip()
    claims = decode(token)
    if not claims or "exp" not in claims:
        return False, "That doesn't look like a Dhan access token."
    if datetime.fromtimestamp(int(claims["exp"]), _IST) <= datetime.now(_IST):
        return False, "This token has already expired — generate a new one on Dhan."
    client = (client_id if client_id is not None else (getattr(get_settings(), meta["client_field"], "") or "")).strip()
    if not client:
        return False, f"No client ID configured for {meta['label']} ({meta['client_env']} in .env)."
    if str(claims.get("dhanClientId", "")) != client:
        return False, (f"This token belongs to a different Dhan client ID than {client}."
                       if client_id is not None else
                       f"This token belongs to a different Dhan client ID than {meta['label']} uses.")
    try:
        r = requests.get("https://api.dhan.co/v2/profile",
                         headers={"access-token": token, "dhanClientId": client, "Accept": "application/json"},
                         timeout=10)
        if r.status_code != 200:
            try:
                why = r.json()
            except Exception:
                why = r.text[:120]
            return False, f"Dhan rejected the token (HTTP {r.status_code}): {str(why)[:160]}"
    except Exception as e:
        return False, f"Could not reach Dhan to check the token: {e}"
    return True, "OK"


def save_token(slot: str, token: str) -> None:
    """Write the token to .env (replacing the old line, or appending) and to this
    process's environment, then reload settings so the new token is in effect."""
    meta = SLOTS[slot]
    write_env({meta["token_env"]: token.strip()})
    logger.info(f"{meta['label']} Dhan token updated (expires {status(slot)['expires_at']})")


def write_env(values: dict) -> None:
    """Set KEY=value lines in .env (replace in place, or append) and in this process's
    environment, then reload settings. Only those values change in the file."""
    for key, token in values.items():
        _write_env_line(key, str(token).strip())
    from config import reload_settings
    reload_settings()


def _write_env_line(key: str, token: str) -> None:
    with _env_lock:
        # newline="" on both read and write: change only the token value and leave every
        # other byte (incl. CRLF/LF line endings) exactly as it was.
        text = _ENV_PATH.open(encoding="utf-8", newline="").read() if _ENV_PATH.exists() else ""
        line = f"{key}={token}"
        pattern = re.compile(rf"^{re.escape(key)}[ \t]*=[^\r\n]*", re.M)
        if pattern.search(text):
            text = pattern.sub(lambda _m: line, text, count=1)
        else:
            eol = "\r\n" if "\r\n" in text else "\n"
            text = text + ("" if (not text or text.endswith("\n")) else eol) + line + eol
        with _ENV_PATH.open("w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.environ[key] = token


# ── Configuration panel: client IDs + Telegram ──────────────────────────────

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV = "TELEGRAM_CHAT_ID"


def get_config() -> dict:
    """What the Configuration panel shows. Client IDs and the Telegram chat ID are
    returned so they can be edited; the bot token and Dhan tokens only masked."""
    cfg = get_settings()
    return {
        "dhan": {slot: {"label": m["label"], "client_id": (getattr(cfg, m["client_field"], "") or "").strip()}
                 for slot, m in SLOTS.items()},
        "telegram": {"bot_token": _mask((cfg.telegram_bot_token or "").strip()),
                     "chat_id": (cfg.telegram_chat_id or "").strip(),
                     "configured": bool(cfg.telegram_bot_token and cfg.telegram_chat_id)},
    }


def save_client_and_token(slot: str, client_id: str, token: str) -> None:
    meta = SLOTS[slot]
    write_env({meta["client_env"]: client_id.strip(), meta["token_env"]: token.strip()})
    logger.info(f"{meta['label']} Dhan client ID + token updated")


def _telegram_base() -> str:
    # Same base the app sends through (a proxy may be set via TELEGRAM_API_URL in .env)
    return os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org").strip().rstrip("/")


def check_telegram_token(token: str) -> tuple[bool, str]:
    """Ask Telegram whether a bot token is valid (getMe)."""
    token = (token or "").strip()
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
        return False, "That doesn't look like a Telegram bot token (expected 123456789:ABC...)."
    try:
        r = requests.get(f"{_telegram_base()}/bot{token}/getMe", timeout=10)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and d.get("ok"):
            return True, f"Bot @{d.get('result', {}).get('username', '?')}"
        return False, f"Telegram rejected the bot token (HTTP {r.status_code}): {str(d.get('description') or r.text)[:120]}"
    except Exception as e:
        return False, f"Could not reach Telegram to check the bot token: {e}"


def check_chat_id(chat_id: str) -> tuple[bool, str]:
    chat_id = (chat_id or "").strip()
    if re.fullmatch(r"-?\d{3,}", chat_id) or re.fullmatch(r"@[A-Za-z0-9_]{4,}", chat_id):
        return True, "OK"
    return False, "Chat ID should be a number (e.g. 123456789, or -100... for a group) or @channelname."
