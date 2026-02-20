"""
Scheduling Intelligence — Learn WHEN to post, not just WHAT to post.

YouTube's algorithm heavily weights early engagement velocity. A video posted
when the audience is asleep gets fewer initial clicks, so the algorithm gives
it less reach. Posting at the right time can 2-5x the views.

This module:
  1. Analyzes historical performance by day-of-week and time-of-day
  2. Cross-references with YouTube Analytics audience activity data
  3. Identifies patterns (e.g., "Tuesday 2pm outperforms Saturday 9am by 3x")
  4. Uses the LLM to interpret patterns and recommend optimal posting windows
  5. Can regenerate calendar slots at better times

The agent currently fills pre-set calendar slots. This module makes those
slots smarter.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import CHANNELS_DIR


def _get_performance_by_time(channel_id: str) -> list[dict]:
    """
    Build a dataset of (publish_day, publish_hour, views, likes, retention)
    for all published videos. This is the raw signal for when content performs.
    """
    from src.agent.analytics import fetch_video_stats, _get_scheduled_times, _is_published

    stats = fetch_video_stats(channel_id)
    schedule_map = _get_scheduled_times(channel_id)

    entries = []
    for v in stats:
        sched_time = schedule_map.get(v["video_id"], "")
        if not _is_published(sched_time):
            continue

        # Determine the actual publish time
        pub_str = sched_time or v.get("published_at", "")
        if not pub_str:
            continue

        try:
            pub_dt = datetime.fromisoformat(pub_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        entries.append({
            "video_id": v["video_id"],
            "title": v["title"],
            "day_of_week": pub_dt.strftime("%A"),
            "hour": pub_dt.hour,
            "day_num": pub_dt.weekday(),
            "views": v["views"],
            "likes": v["likes"],
            "comments": v["comments"],
            "published_at": pub_str,
        })

    return entries


def _aggregate_by_slot(entries: list[dict]) -> dict:
    """
    Group performance data by (day_of_week, hour) and compute averages.
    Returns dict mapping "Monday 14:00" -> {avg_views, count, ...}
    """
    buckets: dict[str, list[dict]] = {}

    for e in entries:
        key = f"{e['day_of_week']} {e['hour']:02d}:00"
        buckets.setdefault(key, []).append(e)

    aggregated = {}
    for key, videos in buckets.items():
        views = [v["views"] for v in videos]
        aggregated[key] = {
            "slot": key,
            "count": len(videos),
            "avg_views": round(sum(views) / len(views)) if views else 0,
            "max_views": max(views) if views else 0,
            "min_views": min(views) if views else 0,
            "total_views": sum(views),
            "titles": [v["title"] for v in videos[:5]],
        }

    return aggregated


def analyze_schedule(channel_id: str) -> dict:
    """
    Full scheduling analysis:
    1. Gather historical performance by publish time
    2. Aggregate by day/hour
    3. Ask the LLM to interpret patterns and recommend optimal times

    Returns analysis dict with recommendations.
    """
    from src.utils.llm import chat_json

    print(f"[Scheduler] Analyzing posting schedule for {channel_id}...")

    entries = _get_performance_by_time(channel_id)
    if len(entries) < 3:
        print(f"[Scheduler] Only {len(entries)} published videos — need more data")
        return {
            "analysis": f"Not enough data ({len(entries)} videos). Need at least 3 published videos.",
            "optimal_slots": [],
            "avoid_slots": [],
        }

    aggregated = _aggregate_by_slot(entries)

    # Build context for the LLM
    lines = [f"Published {len(entries)} videos total.\n"]
    lines.append("Performance by posting time:")

    for slot_key in sorted(aggregated.keys(), key=lambda k: aggregated[k]["avg_views"], reverse=True):
        data = aggregated[slot_key]
        lines.append(
            f"  {slot_key}: {data['count']} videos, "
            f"avg {data['avg_views']:,} views "
            f"(range: {data['min_views']:,}-{data['max_views']:,})"
        )

    # Load current cadence for context
    from src.publishing.calendar_manager import load_calendar
    cal = load_calendar()
    ch_config = cal.get("channels", {}).get(channel_id, {})
    cadence = ch_config.get("cadence", {})
    if cadence:
        days = cadence.get("days", [])
        times = cadence.get("times", [cadence.get("time", "?")])
        tz = cadence.get("timezone", "?")
        lines.append(
            f"\nCurrent schedule: {', '.join(days)} at {', '.join(times)} {tz}"
        )

    context = "\n".join(lines)

    analysis = chat_json(
        "You are a YouTube scheduling strategist. Given performance data "
        "broken down by publish day and time, identify patterns and recommend "
        "optimal posting times.\n\n"
        "Consider:\n"
        "- Which days consistently get more views?\n"
        "- Which hours perform best? (factor in timezone and audience habits)\n"
        "- Are there days/times that consistently underperform?\n"
        "- Is the current posting schedule already optimal, or should it change?\n"
        "- With limited data, express uncertainty rather than over-fitting\n\n"
        "Respond with valid JSON:\n"
        "{\n"
        "  \"analysis\": \"2-3 sentence summary of scheduling patterns\",\n"
        "  \"optimal_slots\": [\n"
        "    {\"day\": \"Monday\", \"hour\": 14, \"why\": \"reason\"},\n"
        "    ...\n"
        "  ],\n"
        "  \"avoid_slots\": [\n"
        "    {\"day\": \"Saturday\", \"hour\": 9, \"why\": \"reason\"}\n"
        "  ],\n"
        "  \"schedule_change_recommended\": true/false,\n"
        "  \"recommended_cadence\": {\n"
        "    \"days\": [\"monday\", \"wednesday\", \"friday\"],\n"
        "    \"times\": [\"14:00\"],\n"
        "    \"reasoning\": \"why this cadence\"\n"
        "  }\n"
        "}",
        context,
        temperature=1.0,
    )

    # Save the report
    report = {
        "channel_id": channel_id,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "videos_analyzed": len(entries),
        "performance_by_slot": aggregated,
        "analysis": analysis,
    }

    report_path = CHANNELS_DIR / channel_id / "schedule_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"[Scheduler] Analysis: {analysis.get('analysis', 'N/A')}")
    if analysis.get("optimal_slots"):
        print(f"[Scheduler] Optimal times:")
        for s in analysis["optimal_slots"][:3]:
            print(f"  - {s.get('day', '?')} at {s.get('hour', '?')}:00 — {s.get('why', '')}")
    if analysis.get("schedule_change_recommended"):
        rec = analysis.get("recommended_cadence", {})
        print(f"[Scheduler] RECOMMENDS schedule change: {rec.get('reasoning', '?')}")

    return analysis


def apply_schedule_change(channel_id: str, force: bool = False) -> bool:
    """
    If a schedule change is recommended, apply it to the calendar.
    Only updates the channel cadence — does NOT delete existing slots.
    New slots will be generated at the new times next time generate_slots runs.

    Returns True if a change was applied.
    """
    report_path = CHANNELS_DIR / channel_id / "schedule_report.json"
    if not report_path.exists():
        return False

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    analysis = report.get("analysis", {})
    if not analysis.get("schedule_change_recommended") and not force:
        return False

    rec = analysis.get("recommended_cadence", {})
    new_days = rec.get("days", [])
    new_times = rec.get("times", [])

    if not new_days or not new_times:
        return False

    from src.publishing.calendar_manager import load_calendar, save_calendar

    cal = load_calendar()
    ch = cal.get("channels", {}).get(channel_id)
    if not ch:
        return False

    old_cadence = ch.get("cadence", {})
    old_days = old_cadence.get("days", [])
    old_times = old_cadence.get("times", [old_cadence.get("time", "12:00")])

    ch["cadence"]["days"] = new_days
    ch["cadence"]["times"] = new_times
    save_calendar(cal)

    print(f"[Scheduler] Schedule updated for {channel_id}:")
    print(f"  Old: {', '.join(old_days)} at {', '.join(old_times)}")
    print(f"  New: {', '.join(new_days)} at {', '.join(new_times)}")
    print(f"  Reason: {rec.get('reasoning', 'LLM recommendation')}")

    # Record as episodic memory
    try:
        from src.agent.memory import record_episode
        record_episode(
            channel_id,
            f"Changed posting schedule: {', '.join(old_days)} at {', '.join(old_times)} "
            f"-> {', '.join(new_days)} at {', '.join(new_times)}. "
            f"Reason: {rec.get('reasoning', '?')[:100]}",
            significance="important",
        )
    except Exception:
        pass

    # Record as belief
    try:
        from src.agent.memory import update_belief
        update_belief(
            channel_id,
            f"Best posting times: {', '.join(new_days)} at {', '.join(new_times)}",
            confidence="observed",
            evidence=f"Schedule analysis of {report.get('videos_analyzed', '?')} videos",
        )
    except Exception:
        pass

    return True


def get_schedule_intelligence(channel_id: str) -> str | None:
    """
    Load the latest schedule report and format for the brain/strategist.
    Returns None if no report exists or it's too old (>30 days).
    """
    report_path = CHANNELS_DIR / channel_id / "schedule_report.json"
    if not report_path.exists():
        return None

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    analyzed_at = report.get("analyzed_at", "")
    try:
        analyzed = datetime.fromisoformat(analyzed_at)
        if analyzed.tzinfo is None:
            analyzed = analyzed.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - analyzed).days > 30:
            return None
    except (ValueError, TypeError):
        pass

    analysis = report.get("analysis", {})
    if not analysis:
        return None

    lines = [f"## Schedule Intelligence (analyzed {analyzed_at[:10]})"]

    if analysis.get("analysis"):
        lines.append(f"Overview: {analysis['analysis']}")

    if analysis.get("optimal_slots"):
        lines.append("\nBest posting times:")
        for s in analysis["optimal_slots"]:
            lines.append(f"  - {s.get('day', '?')} {s.get('hour', '?')}:00 — {s.get('why', '')}")

    if analysis.get("avoid_slots"):
        lines.append("\nAvoid posting at:")
        for s in analysis["avoid_slots"]:
            lines.append(f"  - {s.get('day', '?')} {s.get('hour', '?')}:00 — {s.get('why', '')}")

    if analysis.get("schedule_change_recommended"):
        rec = analysis.get("recommended_cadence", {})
        lines.append(
            f"\nSchedule change recommended: {', '.join(rec.get('days', []))} "
            f"at {', '.join(rec.get('times', []))} — {rec.get('reasoning', '')}"
        )

    return "\n".join(lines)
