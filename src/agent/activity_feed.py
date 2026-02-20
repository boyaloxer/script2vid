"""
Activity Feed — Real-time agent activity log for the dashboard.

The runner writes events here as they happen. The dashboard polls
for new entries every few seconds, creating a live view of what
the agent is doing.

Events are written to a small circular buffer file (last 200 entries)
so it never grows unbounded. Each entry has a monotonic sequence number
so the dashboard can request only entries newer than what it already has.
"""

import json
import time
from pathlib import Path
from threading import Lock

from src.config import CHANNELS_DIR

_FEED_PATH = CHANNELS_DIR / "activity_feed.json"
_MAX_ENTRIES = 200
_lock = Lock()


def _load_feed() -> dict:
    if _FEED_PATH.exists():
        try:
            return json.loads(_FEED_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"seq": 0, "entries": []}


def _save_feed(feed: dict):
    _FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FEED_PATH.write_text(
        json.dumps(feed, ensure_ascii=False), encoding="utf-8"
    )


def emit(event_type: str, message: str, channel_id: str | None = None, **extra):
    """
    Write a new event to the activity feed.

    event_type: "think", "act", "observe", "result", "error", "info"
    message: Human-readable description of what happened
    channel_id: Which channel this relates to (optional)
    extra: Any additional key-value pairs to include
    """
    with _lock:
        feed = _load_feed()
        feed["seq"] += 1
        entry = {
            "seq": feed["seq"],
            "ts": time.time(),
            "type": event_type,
            "message": message,
        }
        if channel_id:
            entry["channel"] = channel_id
        entry.update(extra)
        feed["entries"].append(entry)
        if len(feed["entries"]) > _MAX_ENTRIES:
            feed["entries"] = feed["entries"][-_MAX_ENTRIES:]
        _save_feed(feed)


def get_since(since_seq: int = 0) -> dict:
    """
    Return all entries with seq > since_seq.
    The dashboard calls this with its last-seen seq number
    to get only new entries.
    """
    feed = _load_feed()
    new_entries = [e for e in feed["entries"] if e["seq"] > since_seq]
    return {
        "seq": feed["seq"],
        "entries": new_entries,
    }


def clear():
    """Reset the feed (mainly for testing)."""
    with _lock:
        _save_feed({"seq": 0, "entries": []})
