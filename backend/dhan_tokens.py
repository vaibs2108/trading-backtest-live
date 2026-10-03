"""
dhan_tokens.py — Dhan access tokens: expiry status and updating them from the Settings page.

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


def check_new_token(slot: str, token: str) -> tuple[bool, str]:
    """Validate a pasted token before saving: readable, not expired, belongs to the
    configured client ID, and accepted by Dhan's profile API."""
    meta = SLOTS[slot]
    token = (token or "").strip()
    claims = decode(token)
    if not claims or "exp" not in claims:
        return False, "That doesn't look like a Dhan access token."
    if datetime.fromtimestamp(int(claims["exp"]), _IST) <= datetime.now(_IST):
        return False, "This token has already expired — generate a new one on Dhan."
    client = (getattr(get_settings(), meta["client_field"], "") or "").strip()
    if not client:
        return False, f"No client ID configured for {meta['label']} ({meta['client_env']} in .env)."
    if str(claims.get("dhanClientId", "")) != client:
        return False, f"This token belongs to a different Dhan client ID than {meta['label']} uses."
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
    key, token = meta["token_env"], token.strip()
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
    from config import reload_settings
    reload_settings()
    logger.info(f"{meta['label']} Dhan token updated (expires {status(slot)['expires_at']})")
