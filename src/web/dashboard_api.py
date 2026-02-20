"""
Dashboard API — Surfaces all agent intelligence for the web dashboard.

Each function returns a JSON-serializable dict that the dashboard consumes.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from src.config import CHANNELS_DIR
from src.publishing.calendar_manager import load_calendar, list_channels


def get_overview(channel_id: str | None = None) -> dict:
    """High-level channel health: metrics, strategy, quotas, calendar."""
    channels = list_channels()
    if channel_id:
        channels = {k: v for k, v in channels.items() if k == channel_id}

    result = {"channels": {}, "quotas": {}}

    for ch_id, ch_config in channels.items():
        ch_data = {"name": ch_config.get("name", ch_id), "strategy": None, "metrics_snapshot": None}

        # Strategy
        strat_path = CHANNELS_DIR / ch_id / "content_strategy.json"
        if strat_path.exists():
            try:
                strat = json.loads(strat_path.read_text(encoding="utf-8"))
                ch_data["strategy"] = {
                    "analysis": strat.get("analysis"),
                    "generated_at": strat.get("generated_at"),
                    "topics_planned": len(strat.get("content_plan", [])),
                    "content_plan": strat.get("content_plan", []),
                }
            except (json.JSONDecodeError, OSError):
                pass

        # Latest metrics snapshot
        hist_path = CHANNELS_DIR / ch_id / "metrics_history.json"
        if hist_path.exists():
            try:
                data = json.loads(hist_path.read_text(encoding="utf-8"))
                snapshots = data if isinstance(data, list) else data.get("snapshots", [])
                if snapshots:
                    latest = snapshots[-1]
                    ch_data["metrics_snapshot"] = {
                        "timestamp": latest.get("timestamp"),
                        "video_count": len(latest.get("videos", [])),
                        "total_views": sum(v.get("views", 0) for v in latest.get("videos", [])),
                    }
            except (json.JSONDecodeError, OSError):
                pass

        # Calendar summary
        cal = load_calendar()
        slots = [s for s in cal["slots"] if s["channel_id"] == ch_id]
        now = datetime.now().astimezone()
        future = [s for s in slots if _parse_time(s.get("scheduled_time")) and _parse_time(s["scheduled_time"]) >= now]
        ch_data["calendar"] = {
            "total_slots": len(slots),
            "empty": len([s for s in future if s["status"] == "placeholder"]),
            "assigned": len([s for s in future if s["status"] == "assigned"]),
            "uploaded": len([s for s in future if s["status"] == "uploaded"]),
        }

        # Report freshness
        ch_data["reports"] = {}
        for rname, fname, max_age_days in [
            ("trend_report", "trend_report.json", 7),
            ("audience_report", "audience_report.json", 14),
            ("schedule_report", "schedule_report.json", 30),
        ]:
            rpath = CHANNELS_DIR / ch_id / fname
            if rpath.exists():
                try:
                    rdata = json.loads(rpath.read_text(encoding="utf-8"))
                    ts_key = "scouted_at" if "scouted" in fname else "analyzed_at"
                    ts = rdata.get(ts_key, "")
                    ch_data["reports"][rname] = {"exists": True, "timestamp": ts}
                except (json.JSONDecodeError, OSError):
                    ch_data["reports"][rname] = {"exists": True, "timestamp": None}
            else:
                ch_data["reports"][rname] = {"exists": False}

        result["channels"][ch_id] = ch_data

    # Quotas
    try:
        from src.utils.quota_tracker import get_quota_summary
        result["quotas"] = get_quota_summary()
    except Exception:
        pass

    return result


def get_memory(channel_id: str | None = None) -> dict:
    """Agent's persistent memory: scratchpad, beliefs, episodes."""
    if not channel_id:
        chs = list(list_channels().keys())
        channel_id = chs[0] if chs else None
    if not channel_id:
        return {"error": "No channels configured"}

    mem_path = CHANNELS_DIR / channel_id / "agent_memory.json"
    if not mem_path.exists():
        return {"channel_id": channel_id, "scratchpad": None, "beliefs": [], "episodes": []}

    try:
        data = json.loads(mem_path.read_text(encoding="utf-8"))
        return {
            "channel_id": channel_id,
            "scratchpad": data.get("scratchpad"),
            "beliefs": data.get("beliefs", []),
            "episodes": data.get("episodes", []),
        }
    except (json.JSONDecodeError, OSError):
        return {"channel_id": channel_id, "error": "Failed to read memory"}


def get_experiments(channel_id: str | None = None) -> dict:
    """Active and completed experiments."""
    if not channel_id:
        chs = list(list_channels().keys())
        channel_id = chs[0] if chs else None
    if not channel_id:
        return {"experiments": []}

    exp_path = CHANNELS_DIR / channel_id / "experiments.json"
    if not exp_path.exists():
        return {"channel_id": channel_id, "experiments": []}

    try:
        data = json.loads(exp_path.read_text(encoding="utf-8"))
        experiments = data if isinstance(data, list) else data.get("experiments", [])
        return {"channel_id": channel_id, "experiments": experiments}
    except (json.JSONDecodeError, OSError):
        return {"channel_id": channel_id, "experiments": []}


def get_intelligence(channel_id: str | None = None) -> dict:
    """All intelligence reports: trends, audience, schedule, journal."""
    if not channel_id:
        chs = list(list_channels().keys())
        channel_id = chs[0] if chs else None
    if not channel_id:
        return {"error": "No channels configured"}

    result = {"channel_id": channel_id}

    # Trend report
    trend_path = CHANNELS_DIR / channel_id / "trend_report.json"
    if trend_path.exists():
        try:
            result["trends"] = json.loads(trend_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Audience report
    aud_path = CHANNELS_DIR / channel_id / "audience_report.json"
    if aud_path.exists():
        try:
            result["audience"] = json.loads(aud_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Schedule report
    sched_path = CHANNELS_DIR / channel_id / "schedule_report.json"
    if sched_path.exists():
        try:
            result["schedule"] = json.loads(sched_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Performance journal
    journal_path = CHANNELS_DIR / channel_id / "performance_journal.json"
    if journal_path.exists():
        try:
            jdata = json.loads(journal_path.read_text(encoding="utf-8"))
            entries = jdata.get("entries", [])
            result["journal"] = {
                "total_entries": len(entries),
                "recent": entries[-10:] if entries else [],
                "strategy_reviews": jdata.get("strategy_reviews", []),
            }
        except (json.JSONDecodeError, OSError):
            pass

    return result


def get_recent_sessions(limit: int = 10) -> dict:
    """Recent agent session logs."""
    sessions_dir = CHANNELS_DIR / "agent_sessions"
    if not sessions_dir.exists():
        return {"sessions": []}

    files = sorted(sessions_dir.glob("session_*.json"), reverse=True)[:limit]
    sessions = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "filename": f.name,
                "timestamp": data.get("timestamp"),
                "channels": data.get("channels", []),
                "slots_filled": data.get("slots_filled", 0),
                "iterations": data.get("iterations", 0),
                "log_length": len(data.get("log", [])),
                "log_preview": data.get("log", [])[:5],
            })
        except (json.JSONDecodeError, OSError):
            continue

    return {"sessions": sessions}


def get_dataset_stats() -> dict:
    """Training dataset statistics, flattened for the dashboard."""
    try:
        from src.agent.dataset_builder import get_dataset_stats as _get_stats
        raw = _get_stats()
        gen = raw.get("generations", {})
        return {
            "total_records": raw.get("total_records", 0),
            "decisions": raw.get("decisions", 0),
            "generations": gen.get("total", 0) if isinstance(gen, dict) else gen,
            "linked": gen.get("with_outcome", 0) if isinstance(gen, dict) else 0,
            "awaiting": gen.get("awaiting_outcome", 0) if isinstance(gen, dict) else 0,
            "strategies": raw.get("strategies", 0),
            "export_readiness": raw.get("export_readiness", {}),
            "outcome_distribution": raw.get("outcome_distribution"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_optimizations(channel_id: str | None = None) -> dict:
    """Recent post-publish optimizations."""
    if not channel_id:
        chs = list(list_channels().keys())
        channel_id = chs[0] if chs else None
    if not channel_id:
        return {"optimizations": []}

    opt_path = CHANNELS_DIR / channel_id / "optimization_log.json"
    if not opt_path.exists():
        return {"channel_id": channel_id, "optimizations": []}

    try:
        data = json.loads(opt_path.read_text(encoding="utf-8"))
        return {"channel_id": channel_id, "optimizations": data[-20:] if isinstance(data, list) else []}
    except (json.JSONDecodeError, OSError):
        return {"channel_id": channel_id, "optimizations": []}


def _parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
