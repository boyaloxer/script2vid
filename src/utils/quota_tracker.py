"""
Quota Tracker — Persistent tracking of API usage across sessions.

Tracks cumulative usage for APIs that have daily/monthly quotas
(not just per-minute rate limits). Persists to disk so the agent
knows where it stands even after restarts.

Tracked APIs:
  - ElevenLabs: characters used (monthly quota)
  - LLM: tokens consumed (daily/monthly depending on plan)
  - YouTube Data API: quota units consumed (10,000/day)
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone


_QUOTA_FILE = Path(__file__).resolve().parent.parent.parent / "channels" / "quota_usage.json"


def _load() -> dict:
    if _QUOTA_FILE.exists():
        try:
            return json.loads(_QUOTA_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict):
    _QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QUOTA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── ElevenLabs (character-based, monthly) ─────────────────────

def record_elevenlabs_chars(char_count: int):
    """Record characters sent to ElevenLabs TTS."""
    data = _load()
    month = _this_month()
    el = data.setdefault("elevenlabs", {})

    if el.get("month") != month:
        el["month"] = month
        el["chars_used"] = 0

    el["chars_used"] = el.get("chars_used", 0) + char_count
    el["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save(data)


def get_elevenlabs_usage() -> dict:
    """Get current ElevenLabs usage for this month."""
    data = _load()
    el = data.get("elevenlabs", {})
    month = _this_month()
    if el.get("month") != month:
        return {"month": month, "chars_used": 0}
    return {"month": el.get("month"), "chars_used": el.get("chars_used", 0)}


# ── LLM (token-based, daily) ─────────────────────────────────

def record_llm_tokens(prompt_tokens: int, completion_tokens: int):
    """Record tokens consumed by an LLM call."""
    data = _load()
    today = _today()
    llm = data.setdefault("llm", {})

    if llm.get("date") != today:
        llm["date"] = today
        llm["prompt_tokens"] = 0
        llm["completion_tokens"] = 0
        llm["requests"] = 0

    llm["prompt_tokens"] = llm.get("prompt_tokens", 0) + prompt_tokens
    llm["completion_tokens"] = llm.get("completion_tokens", 0) + completion_tokens
    llm["requests"] = llm.get("requests", 0) + 1
    llm["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save(data)


def get_llm_usage() -> dict:
    """Get current LLM usage for today."""
    data = _load()
    llm = data.get("llm", {})
    today = _today()
    if llm.get("date") != today:
        return {"date": today, "prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
    return {
        "date": llm.get("date"),
        "prompt_tokens": llm.get("prompt_tokens", 0),
        "completion_tokens": llm.get("completion_tokens", 0),
        "total_tokens": llm.get("prompt_tokens", 0) + llm.get("completion_tokens", 0),
        "requests": llm.get("requests", 0),
    }


# ── YouTube Data API (unit-based, daily 10,000 cap) ──────────
# Cost reference:
#   videos.list = 1 unit
#   videos.insert (upload) = 1,600 units
#   videos.update = 50 units
#   search.list = 100 units

_YT_DAILY_QUOTA = 10_000

def record_youtube_units(units: int, operation: str = ""):
    """Record YouTube API quota units consumed."""
    data = _load()
    today = _today()
    yt = data.setdefault("youtube", {})

    if yt.get("date") != today:
        yt["date"] = today
        yt["units_used"] = 0
        yt["operations"] = []

    yt["units_used"] = yt.get("units_used", 0) + units
    if operation:
        yt.setdefault("operations", []).append({
            "op": operation,
            "units": units,
            "time": datetime.now(timezone.utc).isoformat(),
        })
    yt["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save(data)


def get_youtube_usage() -> dict:
    """Get current YouTube API usage for today."""
    data = _load()
    yt = data.get("youtube", {})
    today = _today()
    if yt.get("date") != today:
        return {"date": today, "units_used": 0, "units_remaining": _YT_DAILY_QUOTA}
    used = yt.get("units_used", 0)
    return {
        "date": yt.get("date"),
        "units_used": used,
        "units_remaining": max(0, _YT_DAILY_QUOTA - used),
    }


def can_upload_youtube() -> bool:
    """Check if we have enough YouTube quota for an upload (1,600 units)."""
    usage = get_youtube_usage()
    return usage["units_remaining"] >= 1600


# ── Combined summary for the observer ────────────────────────

def get_quota_summary() -> dict:
    return {
        "elevenlabs": get_elevenlabs_usage(),
        "llm": get_llm_usage(),
        "youtube": get_youtube_usage(),
    }
