"""
Post-Publish Optimizer — Actively improve videos after they go live.

The first 24-48 hours after a video publishes are the critical window.
YouTube's algorithm tests the video with a small audience and decides
whether to push it wider based on CTR and retention.

This module monitors recently published videos and:
  1. Checks early performance (impressions, CTR, views, retention)
  2. Compares against channel averages to spot underperformers
  3. Asks the LLM to suggest title/description improvements
  4. Applies the changes via the YouTube API
  5. Logs every change so the agent learns what optimizations work

This is what separates a "publish and forget" bot from an agent that
actively nurtures its content.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import CHANNELS_DIR


OPTIMIZATION_WINDOW_HOURS = 48
MIN_IMPRESSIONS_TO_EVALUATE = 50


def _get_recently_published(channel_id: str) -> list[dict]:
    """
    Find videos that published within the optimization window.
    Returns list of dicts with video_id, title, scheduled_time, slot_id.
    """
    from src.publishing.calendar_manager import load_calendar

    cal = load_calendar()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=OPTIMIZATION_WINDOW_HOURS)

    recent = []
    for slot in cal["slots"]:
        if slot["channel_id"] != channel_id:
            continue
        if not slot.get("youtube_video_id"):
            continue
        if slot["status"] not in ("uploaded",):
            continue

        sched = slot.get("scheduled_time", "")
        if not sched:
            continue

        try:
            pub_time = datetime.fromisoformat(sched)
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        if cutoff <= pub_time <= now:
            hours_live = (now - pub_time).total_seconds() / 3600
            recent.append({
                "video_id": slot["youtube_video_id"],
                "title": slot.get("title", ""),
                "description": slot.get("description", ""),
                "scheduled_time": sched,
                "slot_id": slot["id"],
                "hours_live": round(hours_live, 1),
            })

    return recent


def _get_video_early_stats(channel_id: str, video_ids: list[str]) -> dict[str, dict]:
    """
    Fetch early performance data for recently published videos.
    Uses both Data API (views, likes) and Analytics API (impressions, CTR).
    """
    from src.agent.analytics import _get_youtube_service, _get_analytics_service

    stats = {}

    # Basic stats from Data API
    yt = _get_youtube_service(channel_id)
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = yt.videos().list(
                part="statistics",
                id=",".join(batch),
            ).execute()
            for item in resp.get("items", []):
                s = item.get("statistics", {})
                stats[item["id"]] = {
                    "views": int(s.get("viewCount", 0)),
                    "likes": int(s.get("likeCount", 0)),
                    "comments": int(s.get("commentCount", 0)),
                }
        except Exception:
            pass

    # Deep stats from Analytics API (impressions, CTR)
    yt_analytics = _get_analytics_service(channel_id)
    if yt_analytics:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            resp = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                dimensions="video",
                filters=f"video=={','.join(video_ids)}",
                metrics=(
                    "views,estimatedMinutesWatched,averageViewDuration,"
                    "averageViewPercentage"
                ),
            ).execute()

            for row in resp.get("rows", []):
                vid = row[0]
                if vid not in stats:
                    stats[vid] = {}
                stats[vid].update({
                    "analytics_views": row[1],
                    "watch_time_min": round(row[2], 1),
                    "avg_view_duration_s": row[3],
                    "avg_view_percentage": round(row[4], 1),
                })
        except Exception:
            pass

    return stats


def _get_channel_averages(channel_id: str) -> dict:
    """
    Get channel average metrics to compare newly published videos against.
    Loads from the most recent metrics snapshot if available.
    """
    history_path = CHANNELS_DIR / channel_id / "metrics_history.json"
    if not history_path.exists():
        return {}

    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        snapshots = data if isinstance(data, list) else data.get("snapshots", [])
        if not snapshots:
            return {}

        latest = snapshots[-1]
        videos = latest.get("videos", [])
        if not videos:
            return {}

        views = [v.get("views", 0) for v in videos if v.get("views", 0) > 0]
        if not views:
            return {}

        return {
            "avg_views": sum(views) / len(views),
            "median_views": sorted(views)[len(views) // 2],
            "video_count": len(views),
        }
    except (json.JSONDecodeError, OSError):
        return {}


def _load_optimization_log(channel_id: str) -> list[dict]:
    log_path = CHANNELS_DIR / channel_id / "optimization_log.json"
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_optimization_log(channel_id: str, log: list[dict]):
    log_path = CHANNELS_DIR / channel_id / "optimization_log.json"
    log_path.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _already_optimized(channel_id: str, video_id: str) -> bool:
    """Check if we've already optimized this video in this window."""
    log = _load_optimization_log(channel_id)
    now = datetime.now(timezone.utc)

    for entry in log:
        if entry.get("video_id") != video_id:
            continue
        try:
            opt_time = datetime.fromisoformat(entry["optimized_at"])
            if opt_time.tzinfo is None:
                opt_time = opt_time.replace(tzinfo=timezone.utc)
            if (now - opt_time).total_seconds() < OPTIMIZATION_WINDOW_HOURS * 3600:
                return True
        except (ValueError, TypeError):
            continue

    return False


def evaluate_and_optimize(channel_id: str) -> list[dict]:
    """
    Main optimization pipeline:
    1. Find recently published videos
    2. Check their early performance
    3. Compare against channel averages
    4. If underperforming, ask LLM for title/description improvements
    5. Apply changes and log them

    Returns list of optimization actions taken.
    """
    from src.utils.llm import chat_json
    from src.publishing.publisher import update_video_metadata

    print(f"[Optimizer] Checking recently published videos for {channel_id}...")

    recent = _get_recently_published(channel_id)
    if not recent:
        print("[Optimizer] No recently published videos in the optimization window.")
        return []

    print(f"[Optimizer] Found {len(recent)} video(s) in the {OPTIMIZATION_WINDOW_HOURS}h window")

    # Filter out already-optimized videos
    candidates = [v for v in recent if not _already_optimized(channel_id, v["video_id"])]
    if not candidates:
        print("[Optimizer] All recent videos already optimized.")
        return []

    # Get early stats
    video_ids = [v["video_id"] for v in candidates]
    early_stats = _get_video_early_stats(channel_id, video_ids)
    channel_avgs = _get_channel_averages(channel_id)

    actions = []

    for video in candidates:
        vid = video["video_id"]
        stats = early_stats.get(vid, {})
        views = stats.get("views", 0)
        hours_live = video["hours_live"]

        # Skip if too early (not enough data)
        if hours_live < 6:
            print(f"[Optimizer] {vid} only {hours_live}h live — too early to evaluate")
            continue

        # Build context for the LLM
        performance_ctx = (
            f"Video: \"{video['title']}\"\n"
            f"Hours live: {hours_live}\n"
            f"Views: {views}\n"
            f"Likes: {stats.get('likes', 0)}\n"
            f"Comments: {stats.get('comments', 0)}\n"
        )
        if stats.get("avg_view_percentage"):
            performance_ctx += f"Avg view percentage: {stats['avg_view_percentage']}%\n"
        if stats.get("avg_view_duration_s"):
            performance_ctx += f"Avg view duration: {stats['avg_view_duration_s']}s\n"

        if channel_avgs:
            performance_ctx += (
                f"\nChannel averages (for comparison):\n"
                f"  Avg views per video: {channel_avgs['avg_views']:.0f}\n"
                f"  Median views: {channel_avgs['median_views']}\n"
            )

        # Ask the LLM if optimization is needed
        decision = chat_json(
            "You are a YouTube optimization specialist. A video was recently "
            "published and you're checking its early performance.\n\n"
            "Decide if the title or description should be changed to improve "
            "click-through rate (CTR). Consider:\n"
            "- Is the view count lower than expected for this time window?\n"
            "- Could the title be more compelling, curiosity-driving, or emotional?\n"
            "- Is the title too long, too vague, or not attention-grabbing enough?\n"
            "- Could the description hook be stronger?\n\n"
            "IMPORTANT: Only suggest changes if you genuinely believe they'll "
            "improve performance. Don't change things that are already working.\n"
            "Changing a title that's performing well can HURT performance.\n\n"
            "Respond with valid JSON:\n"
            "{\n"
            "  \"should_optimize\": true/false,\n"
            "  \"reasoning\": \"why or why not\",\n"
            "  \"new_title\": \"improved title (or null if no change)\",\n"
            "  \"new_description\": \"improved description (or null if no change)\"\n"
            "}",
            performance_ctx,
            temperature=1.0,
        )

        should_optimize = decision.get("should_optimize", False)
        reasoning = decision.get("reasoning", "")
        new_title = decision.get("new_title")
        new_description = decision.get("new_description")

        print(f"[Optimizer] {vid} ({hours_live}h, {views} views): "
              f"{'OPTIMIZE' if should_optimize else 'KEEP'} — {reasoning[:100]}")

        log_entry = {
            "video_id": vid,
            "original_title": video["title"],
            "hours_live": hours_live,
            "views_at_check": views,
            "stats_at_check": stats,
            "channel_averages": channel_avgs,
            "should_optimize": should_optimize,
            "reasoning": reasoning,
            "optimized_at": datetime.now(timezone.utc).isoformat(),
        }

        if should_optimize and (new_title or new_description):
            try:
                update_result = update_video_metadata(
                    video_id=vid,
                    title=new_title,
                    description=new_description,
                    channel_id=channel_id,
                )
                log_entry["new_title"] = new_title
                log_entry["new_description"] = new_description
                log_entry["applied"] = True

                print(f"[Optimizer] Updated {vid}:")
                if new_title:
                    print(f"  Title: \"{video['title']}\" -> \"{new_title}\"")
                if new_description:
                    print(f"  Description updated")

                actions.append(log_entry)

                # Record as episodic memory
                try:
                    from src.agent.memory import record_episode
                    record_episode(
                        channel_id,
                        f"Optimized \"{video['title']}\" -> \"{new_title or video['title']}\" "
                        f"at {hours_live}h ({views} views). Reason: {reasoning[:80]}",
                        significance="normal",
                    )
                except Exception:
                    pass

            except Exception as e:
                print(f"[Optimizer] Failed to update {vid}: {e}")
                log_entry["applied"] = False
                log_entry["error"] = str(e)
        else:
            log_entry["applied"] = False

        # Save to log regardless
        opt_log = _load_optimization_log(channel_id)
        opt_log.append(log_entry)
        _save_optimization_log(channel_id, opt_log)

    print(f"[Optimizer] {len(actions)} optimization(s) applied")
    return actions


def get_optimization_summary(channel_id: str) -> str | None:
    """Format recent optimization history for the brain/observer."""
    log = _load_optimization_log(channel_id)
    if not log:
        return None

    now = datetime.now(timezone.utc)
    recent = []
    for entry in log:
        try:
            opt_time = datetime.fromisoformat(entry["optimized_at"])
            if opt_time.tzinfo is None:
                opt_time = opt_time.replace(tzinfo=timezone.utc)
            if (now - opt_time).days <= 7:
                recent.append(entry)
        except (ValueError, TypeError):
            continue

    if not recent:
        return None

    applied = [e for e in recent if e.get("applied")]
    skipped = [e for e in recent if not e.get("applied")]

    lines = [f"## Recent Optimizations (last 7 days)"]
    lines.append(f"Checked: {len(recent)} videos, Optimized: {len(applied)}, Kept: {len(skipped)}")

    for e in applied:
        old_t = e.get("original_title", "?")
        new_t = e.get("new_title", old_t)
        lines.append(
            f"  - \"{old_t}\" -> \"{new_t}\" "
            f"(at {e.get('hours_live', '?')}h, {e.get('views_at_check', '?')} views)"
        )

    return "\n".join(lines)
