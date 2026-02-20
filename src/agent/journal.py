"""
Performance Journal — Links strategies to outcomes over time.

Maintains a persistent, append-only log of:
  - What strategy was active when each video was produced
  - How each video performed after N days
  - Strategy-level aggregates (did strategy X outperform strategy Y?)
  - The agent's own reflections on what it learned

This is the agent's long-term memory — it reads this before planning
new strategies so it doesn't repeat mistakes.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from src.config import CHANNELS_DIR


def _journal_path(channel_id: str) -> Path:
    return CHANNELS_DIR / channel_id / "performance_journal.json"


def _load_journal(channel_id: str) -> dict:
    path = _journal_path(channel_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"entries": [], "strategy_reviews": []}


def _save_journal(channel_id: str, journal: dict):
    path = _journal_path(channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal, indent=2), encoding="utf-8")


def record_video_produced(
    channel_id: str,
    title: str,
    video_id: str | None,
    strategy_topic: str | None,
    strategy_analysis: str | None,
    review_score: int | None,
    scheduled_time: str | None,
):
    """Record that a video was produced, linking it to the active strategy."""
    journal = _load_journal(channel_id)

    journal["entries"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "video_id": video_id,
        "strategy_topic": strategy_topic,
        "strategy_analysis": strategy_analysis,
        "review_score": review_score,
        "scheduled_time": scheduled_time,
        "performance": None,  # filled in later by update_video_performance
    })

    _save_journal(channel_id, journal)


def update_video_performance(channel_id: str, video_id: str, metrics: dict):
    """
    Update the performance data for a video in the journal.
    Called during metrics analysis to retroactively fill in how videos did.
    """
    journal = _load_journal(channel_id)

    for entry in journal["entries"]:
        if entry.get("video_id") == video_id:
            entry["performance"] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "views": metrics.get("views", 0),
                "likes": metrics.get("likes", 0),
                "comments": metrics.get("comments", 0),
                "avg_view_percentage": metrics.get("avg_view_percentage"),
                "avg_view_duration_s": metrics.get("avg_view_duration_s"),
                "subscribers_gained": metrics.get("subscribers_gained"),
            }
            break

    _save_journal(channel_id, journal)


def record_strategy_review(
    channel_id: str,
    strategy_analysis: str,
    videos_produced: int,
    avg_views: float,
    avg_retention: float | None,
    reflection: str,
):
    """
    Record a strategy-level review: how well did this batch of content perform?
    The reflection is the agent's own assessment of what worked and what didn't.
    """
    journal = _load_journal(channel_id)

    journal["strategy_reviews"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy_analysis": strategy_analysis,
        "videos_produced": videos_produced,
        "avg_views": avg_views,
        "avg_retention": avg_retention,
        "reflection": reflection,
    })

    _save_journal(channel_id, journal)


def get_learnings_summary(channel_id: str) -> str | None:
    """
    Build a summary of past learnings for the strategist to read
    before creating a new content plan.
    """
    journal = _load_journal(channel_id)

    if not journal["entries"] and not journal["strategy_reviews"]:
        return None

    lines = ["## Performance Journal (agent's long-term memory)\n"]

    # Strategy reviews (high-level lessons)
    if journal["strategy_reviews"]:
        lines.append("### Past Strategy Reviews\n")
        for r in journal["strategy_reviews"][-5:]:
            lines.append(f"  [{r['timestamp'][:10]}] {r['videos_produced']} videos produced")
            lines.append(f"    Avg views: {r['avg_views']:.0f}")
            if r.get("avg_retention"):
                lines.append(f"    Avg retention: {r['avg_retention']:.1f}%")
            lines.append(f"    Reflection: {r['reflection']}")
            lines.append("")

    # Individual video outcomes linked to strategy topics
    entries_with_perf = [e for e in journal["entries"] if e.get("performance")]
    if entries_with_perf:
        lines.append("### Video Outcomes (linked to strategy)\n")

        # Group by strategy topic
        by_topic: dict[str, list] = {}
        for e in entries_with_perf:
            topic = e.get("strategy_topic") or "No strategy"
            by_topic.setdefault(topic, []).append(e)

        for topic, entries in by_topic.items():
            avg_views = sum(e["performance"]["views"] for e in entries) / len(entries)
            retentions = [
                e["performance"]["avg_view_percentage"]
                for e in entries if e["performance"].get("avg_view_percentage")
            ]
            avg_ret = sum(retentions) / len(retentions) if retentions else None

            lines.append(f"  Strategy topic: \"{topic}\"")
            lines.append(f"    Videos: {len(entries)}, Avg views: {avg_views:.0f}")
            if avg_ret:
                lines.append(f"    Avg retention: {avg_ret:.1f}%")
            for e in entries:
                p = e["performance"]
                ret_str = f", {p['avg_view_percentage']}% retention" if p.get("avg_view_percentage") else ""
                lines.append(
                    f"    - \"{e['title']}\" — {p['views']} views, "
                    f"{p['likes']} likes{ret_str}"
                )
            lines.append("")

    return "\n".join(lines) if len(lines) > 1 else None
