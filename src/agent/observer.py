"""
Observer — Builds a snapshot of the world state for the agent brain.

Checks: calendar slots, API health, pipeline progress, channel metrics,
recent errors, and rate limits. Returns a structured dict the planner
can reason about.
"""

import json
import time
import requests
from pathlib import Path
from datetime import datetime

from src.config import (
    CHANNELS_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    PEXELS_API_KEY, PEXELS_BASE_URL, ELEVENLABS_API_KEY, ELEVENLABS_BASE_URL,
)
from src.publishing.calendar_manager import load_calendar, get_upcoming


def check_api_health() -> dict:
    """Ping each API and return status + latency."""
    results = {}

    # LLM API
    try:
        start = time.time()
        resp = requests.get(
            f"{LLM_BASE_URL}/models",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=10,
        )
        latency = round(time.time() - start, 2)
        results["llm"] = {
            "status": "ok" if resp.status_code == 200 else f"error ({resp.status_code})",
            "latency_s": latency,
            "model": LLM_MODEL,
        }
    except Exception as e:
        results["llm"] = {"status": f"unreachable: {e}", "latency_s": None}

    # Pexels API
    try:
        start = time.time()
        resp = requests.get(
            f"{PEXELS_BASE_URL}/popular?per_page=1",
            headers={"Authorization": PEXELS_API_KEY},
            timeout=10,
        )
        latency = round(time.time() - start, 2)
        remaining = resp.headers.get("X-Ratelimit-Remaining")
        results["pexels"] = {
            "status": "ok" if resp.status_code == 200 else f"error ({resp.status_code})",
            "latency_s": latency,
            "rate_limit_remaining": int(remaining) if remaining else None,
        }
    except Exception as e:
        results["pexels"] = {"status": f"unreachable: {e}", "latency_s": None}

    # ElevenLabs API — use /voices endpoint (works with any valid key)
    try:
        start = time.time()
        resp = requests.get(
            f"{ELEVENLABS_BASE_URL}/voices",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            timeout=10,
        )
        latency = round(time.time() - start, 2)
        results["elevenlabs"] = {
            "status": "ok" if resp.status_code == 200 else f"error ({resp.status_code})",
            "latency_s": latency,
        }
    except Exception as e:
        results["elevenlabs"] = {"status": f"unreachable: {e}", "latency_s": None}

    return results


def check_calendar_state() -> dict:
    """Summarize calendar: empty slots per channel, next deadlines."""
    cal = load_calendar()
    now = datetime.now().astimezone()
    channels_state = {}

    for ch_id in cal.get("channels", {}):
        upcoming = get_upcoming(ch_id)
        empty = [s for s in upcoming if s["status"] == "placeholder"]
        uploaded = [s for s in upcoming if s["status"] == "uploaded"]
        assigned = [s for s in upcoming if s["status"] == "assigned"]

        next_empty_time = None
        if empty:
            next_empty_time = empty[0]["scheduled_time"]

        strategy_path = CHANNELS_DIR / ch_id / "content_strategy.json"
        has_strategy = strategy_path.exists()
        strategy_age_days = None
        if has_strategy:
            import datetime as _dt
            mtime = _dt.datetime.fromtimestamp(strategy_path.stat().st_mtime)
            strategy_age_days = (_dt.datetime.now() - mtime).days

        trend_report_path = CHANNELS_DIR / ch_id / "trend_report.json"
        has_trend_report = trend_report_path.exists()
        trend_report_age_days = None
        if has_trend_report:
            import datetime as _dt
            try:
                report = json.loads(trend_report_path.read_text(encoding="utf-8"))
                scouted_at = report.get("scouted_at", "")
                scouted = _dt.datetime.fromisoformat(scouted_at)
                if scouted.tzinfo is None:
                    scouted = scouted.replace(tzinfo=_dt.timezone.utc)
                trend_report_age_days = (_dt.datetime.now(_dt.timezone.utc) - scouted).days
            except (ValueError, TypeError, json.JSONDecodeError, OSError):
                trend_report_age_days = None

        audience_report_path = CHANNELS_DIR / ch_id / "audience_report.json"
        has_audience_report = audience_report_path.exists()
        audience_report_age_days = None
        if has_audience_report:
            import datetime as _dt
            try:
                a_report = json.loads(audience_report_path.read_text(encoding="utf-8"))
                a_at = a_report.get("analyzed_at", "")
                a_dt = _dt.datetime.fromisoformat(a_at)
                if a_dt.tzinfo is None:
                    a_dt = a_dt.replace(tzinfo=_dt.timezone.utc)
                audience_report_age_days = (_dt.datetime.now(_dt.timezone.utc) - a_dt).days
            except (ValueError, TypeError, json.JSONDecodeError, OSError):
                audience_report_age_days = None

        # Count videos in the optimization window (published in last 48h)
        videos_in_opt_window = 0
        try:
            import datetime as _dt
            opt_cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=48)
            for s in upcoming:
                if s["status"] != "uploaded" or not s.get("scheduled_time"):
                    continue
                try:
                    st = _dt.datetime.fromisoformat(s["scheduled_time"])
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=_dt.timezone.utc)
                    if opt_cutoff <= st <= _dt.datetime.now(_dt.timezone.utc):
                        videos_in_opt_window += 1
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

        # Count total published videos for this channel
        total_published = 0
        try:
            all_slots = load_calendar().get("slots", [])
            total_published = sum(
                1 for s in all_slots
                if s.get("channel_id") == ch_id
                and s.get("status") == "uploaded"
            )
        except Exception:
            pass

        channels_state[ch_id] = {
            "empty_slots": len(empty),
            "assigned_slots": len(assigned),
            "uploaded_slots": len(uploaded),
            "total_published": total_published,
            "next_empty_slot": next_empty_time,
            "has_content_prompt": (CHANNELS_DIR / ch_id / "content_prompt.md").exists(),
            "has_youtube_token": (CHANNELS_DIR / ch_id / "youtube_token.json").exists(),
            "has_strategy": has_strategy,
            "strategy_age_days": strategy_age_days,
            "has_trend_report": has_trend_report,
            "trend_report_age_days": trend_report_age_days,
            "has_audience_report": has_audience_report,
            "audience_report_age_days": audience_report_age_days,
            "has_schedule_report": (CHANNELS_DIR / ch_id / "schedule_report.json").exists(),
            "videos_in_optimization_window": videos_in_opt_window,
        }

    return channels_state


_STALE_PIPELINE_THRESHOLD_S = 1800  # 30 min without update = stale


def check_running_pipelines() -> list[dict]:
    """Check for any currently running pipelines by reading _progress.json files."""
    running = []
    if not CHANNELS_DIR.exists():
        return running

    now = time.time()

    for progress_file in CHANNELS_DIR.rglob("_progress.json"):
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            file_mtime = progress_file.stat().st_mtime
            seconds_since_update = now - file_mtime

            if data.get("status") == "running":
                is_stale = seconds_since_update > _STALE_PIPELINE_THRESHOLD_S
                running.append({
                    "project": data.get("project_name"),
                    "current_stage": data.get("current_stage"),
                    "elapsed_s": data.get("elapsed_s"),
                    "seconds_since_update": int(seconds_since_update),
                    "stale": is_stale,
                    "stages": {
                        k: v.get("status")
                        for k, v in data.get("stages", {}).items()
                    },
                    "path": str(progress_file.parent),
                })
            elif data.get("status") == "complete":
                running.append({
                    "project": data.get("project_name"),
                    "current_stage": "complete",
                    "elapsed_s": data.get("elapsed_s"),
                    "output_path": data.get("output_path"),
                    "path": str(progress_file.parent),
                })
        except (json.JSONDecodeError, OSError):
            continue

    return running


def check_quotas() -> dict:
    """Get current API quota usage."""
    try:
        from src.utils.quota_tracker import get_quota_summary
        return get_quota_summary()
    except Exception:
        return {}


def build_world_state(channel_filter: list[str] | None = None) -> dict:
    """
    Build a complete snapshot of the world for the agent brain.
    This is what the LLM reasons about when deciding what to do next.
    """
    state = {
        "timestamp": datetime.now().isoformat(),
        "apis": check_api_health(),
        "calendar": check_calendar_state(),
        "pipelines": check_running_pipelines(),
        "quotas": check_quotas(),
        "user_commands": [],
    }
    if channel_filter:
        state["calendar"] = {k: v for k, v in state["calendar"].items() if k in channel_filter}

    try:
        from src.agent.command_queue import get_pending
        state["user_commands"] = get_pending()
    except Exception:
        pass

    return state


def world_state_to_text(state: dict) -> str:
    """Convert the world state dict to a human-readable summary for the LLM."""
    lines = [f"Current time: {state['timestamp']}", ""]

    # API health
    lines.append("## API Health")
    for name, info in state["apis"].items():
        status = info.get("status", "unknown")
        extra = ""
        if info.get("rate_limit_remaining") is not None:
            extra += f", {info['rate_limit_remaining']} requests remaining"
        if info.get("characters_remaining") is not None:
            extra += f", {info['characters_remaining']:,} chars remaining"
        if info.get("latency_s") is not None:
            extra += f", {info['latency_s']}s latency"
        lines.append(f"  {name}: {status}{extra}")

    # Calendar
    lines.append("\n## Calendar State")
    for ch_id, info in state["calendar"].items():
        lines.append(f"  {ch_id}:")
        total_pub = info.get("total_published", 0)
        lines.append(f"    Published videos (all time): {total_pub}")
        lines.append(f"    Empty slots: {info['empty_slots']}")
        lines.append(f"    Uploaded: {info['uploaded_slots']}")
        if info["next_empty_slot"]:
            lines.append(f"    Next empty: {info['next_empty_slot']}")
        if info.get("has_strategy"):
            age = info.get("strategy_age_days")
            lines.append(f"    Strategy: exists ({age}d old)" if age is not None else "    Strategy: exists")
        else:
            lines.append(f"    Strategy: NONE")
        if info.get("has_trend_report"):
            t_age = info.get("trend_report_age_days")
            stale = " (STALE — >7 days)" if t_age is not None and t_age > 7 else ""
            lines.append(f"    Trend report: exists ({t_age}d old){stale}" if t_age is not None else "    Trend report: exists")
        else:
            lines.append(f"    Trend report: NONE (scout_trends needed)")
        if info.get("has_audience_report"):
            a_age = info.get("audience_report_age_days")
            stale = " (STALE — >14 days)" if a_age is not None and a_age > 14 else ""
            lines.append(f"    Audience report: exists ({a_age}d old){stale}" if a_age is not None else "    Audience report: exists")
        else:
            lines.append(f"    Audience report: NONE (analyze_audience needed)")
        if not info.get("has_schedule_report"):
            lines.append(f"    Schedule report: NONE (analyze_schedule when 5+ videos)")
        opt_count = info.get("videos_in_optimization_window", 0)
        if opt_count > 0:
            lines.append(f"    Videos in optimization window (48h): {opt_count}")
        if not info["has_content_prompt"]:
            lines.append(f"    WARNING: No content_prompt.md")
        if not info["has_youtube_token"]:
            lines.append(f"    WARNING: No YouTube token")

    # Quotas
    quotas = state.get("quotas", {})
    if quotas:
        lines.append("\n## API Quotas")
        el = quotas.get("elevenlabs", {})
        if el.get("chars_used", 0) > 0:
            lines.append(f"  ElevenLabs: {el['chars_used']:,} chars used this month")
        llm = quotas.get("llm", {})
        if llm.get("requests", 0) > 0:
            lines.append(
                f"  LLM: {llm.get('total_tokens', llm.get('prompt_tokens', 0) + llm.get('completion_tokens', 0)):,} tokens today "
                f"({llm['requests']} requests)"
            )
        yt = quotas.get("youtube", {})
        if yt:
            lines.append(
                f"  YouTube API: {yt.get('units_used', 0):,}/10,000 units today "
                f"({yt.get('units_remaining', 10000):,} remaining, "
                f"~{yt.get('units_remaining', 10000) // 1600} uploads left)"
            )

    # Running pipelines
    if state["pipelines"]:
        lines.append("\n## Running Pipelines")
        for p in state["pipelines"]:
            stage = p.get("current_stage", "unknown")
            elapsed = p.get("elapsed_s", "?")
            stale = p.get("stale", False)
            status = "STALE (likely crashed)" if stale else "active"
            since = p.get("seconds_since_update")
            extra = f", last update {since}s ago" if since else ""
            lines.append(f"  {p['project']}: stage={stage}, elapsed={elapsed}s, {status}{extra}")

    # Experiments
    try:
        from src.agent.experiment_engine import get_experiments_summary
        for ch_id in state["calendar"]:
            exp_text = get_experiments_summary(ch_id)
            if exp_text:
                lines.append(f"\n{exp_text}")
    except Exception:
        pass

    # Recent optimizations
    try:
        from src.agent.optimizer import get_optimization_summary
        for ch_id in state["calendar"]:
            opt_text = get_optimization_summary(ch_id)
            if opt_text:
                lines.append(f"\n{opt_text}")
    except Exception:
        pass

    # Training dataset status
    try:
        from src.agent.dataset_builder import get_dataset_stats_text
        ds_text = get_dataset_stats_text()
        if ds_text:
            lines.append(f"\n{ds_text}")
    except Exception:
        pass

    # User commands from dashboard
    cmds = state.get("user_commands", [])
    if cmds:
        lines.append("\n## USER COMMANDS (PRIORITY — handle these first)")
        for c in cmds:
            lines.append(f"  [cmd #{c['id']}] \"{c['text']}\" (from {c['source']})")
        lines.append("  Map each command to the best matching action and execute it.")

    return "\n".join(lines)
