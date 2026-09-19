"""Audit log (F9): append-only, in memory + logs/audit.jsonl."""
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "logs" / "audit.jsonl"
_events: list[dict] = []
_lock = threading.Lock()


def fingerprint(content) -> str:
    raw = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:8]


def log_event(event: str, actor: str, result: str, detail: str = "", content=None) -> dict:
    entry = {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": actor,
        "event": event,
        "result": result,
        "detail": detail,
        "contentId": fingerprint(content) if content is not None else None,
    }
    with _lock:
        _events.append(entry)
        try:
            LOG_FILE.parent.mkdir(exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # logging must never break a request
    return entry


def get_events() -> list[dict]:
    with _lock:
        return list(_events)
