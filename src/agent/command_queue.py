"""
Command Queue — lets users send instructions to the agent from the dashboard.

Commands are stored in a JSON file and checked by the observer during each
think-act cycle. The agent prioritizes pending user commands over its normal
workflow.
"""

import json
import time
from pathlib import Path
from threading import Lock

from src.config import CHANNELS_DIR

_QUEUE_PATH = CHANNELS_DIR / "command_queue.json"
_lock = Lock()


def _load() -> dict:
    if _QUEUE_PATH.exists():
        try:
            return json.loads(_QUEUE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"next_id": 1, "commands": []}


def _save(data: dict):
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def push_command(text: str, source: str = "dashboard") -> dict:
    """Add a user command to the queue. Returns the created command."""
    with _lock:
        data = _load()
        cmd = {
            "id": data["next_id"],
            "text": text.strip(),
            "source": source,
            "ts": time.time(),
            "status": "pending",
        }
        data["next_id"] += 1
        data["commands"].append(cmd)
        # Keep only last 50 commands
        if len(data["commands"]) > 50:
            data["commands"] = data["commands"][-50:]
        _save(data)
        return cmd


def get_pending() -> list[dict]:
    """Return all pending (unhandled) commands."""
    data = _load()
    return [c for c in data["commands"] if c.get("status") == "pending"]


def mark_done(cmd_id: int, result: str = ""):
    """Mark a command as handled by the agent."""
    with _lock:
        data = _load()
        for c in data["commands"]:
            if c.get("id") == cmd_id:
                c["status"] = "done"
                c["result"] = result
                c["handled_at"] = time.time()
        _save(data)


def get_recent(limit: int = 20) -> list[dict]:
    """Return the most recent commands (for dashboard display)."""
    data = _load()
    return list(reversed(data["commands"][-limit:]))
