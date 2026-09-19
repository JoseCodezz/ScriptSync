"""Shared signing + freshness helpers (F10).

Brand agents sign each response; the assistant verifies it. This build uses
HMAC-SHA256 with a shared DEMO key. It is NOT an ANS identity key - say so in the
pitch/Devpost. If ANS identity keys become available, only this file changes.
"""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

SIGNING_MODE = "demo-hmac"
DEMO_KEY = os.environ.get("SCRIPTSYNC_DEMO_KEY", "scriptsync-demo-key").encode()
MAX_AGE = timedelta(hours=24)
SIGNED_FIELDS = ("agentName", "brand", "answers", "refused", "timestamp")


def _canonical(resp: dict) -> bytes:
    body = {k: resp.get(k) for k in SIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign_response(resp: dict, key: bytes = DEMO_KEY) -> str:
    return base64.b64encode(hmac.new(key, _canonical(resp), hashlib.sha256).digest()).decode()


def verify_signature(resp: dict, key: bytes = DEMO_KEY) -> bool:
    sig = resp.get("signature") or ""
    return hmac.compare_digest(sign_response(resp, key), sig)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def check_freshness(resp: dict, now: datetime | None = None):
    """Return (fresh, age_hours). Missing/garbled timestamp counts as not fresh."""
    now = now or datetime.now(timezone.utc)
    try:
        age = now - parse_ts(resp.get("timestamp", ""))
    except (ValueError, TypeError):
        return False, None
    return age <= MAX_AGE, round(age.total_seconds() / 3600, 1)
