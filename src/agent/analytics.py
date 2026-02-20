"""
YouTube Analytics — Pull per-video and channel-level metrics.

Uses BOTH APIs:
  - YouTube Data API v3: video metadata, basic stats (views, likes, comments)
  - YouTube Analytics API v2: deep metrics (watch time, retention, CTR,
    impressions, traffic sources, subscriber gain/loss)

The Analytics API requires the yt-analytics.readonly OAuth scope, which
must be included in the token. If the token doesn't have it, we fall
back to Data API-only metrics.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.config import CHANNELS_DIR
from src.publishing.calendar_manager import load_calendar

_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
_DATA_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def _get_youtube_service(channel_id: str):
    """Build an authenticated YouTube Data API client for a channel."""
    token_path = CHANNELS_DIR / channel_id / "youtube_token.json"
    if not token_path.exists():
        raise FileNotFoundError(
            f"No YouTube token for channel '{channel_id}'. "
            f"Run a publish command first to authenticate."
        )
    scopes = [_DATA_SCOPE]
    creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def _get_analytics_service(channel_id: str):
    """
    Build an authenticated YouTube Analytics API client.
    Returns None if the token doesn't have the analytics scope.
    """
    token_path = CHANNELS_DIR / channel_id / "youtube_token.json"
    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.scopes and _ANALYTICS_SCOPE not in creds.scopes:
        return None

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")

    try:
        return build("youtubeAnalytics", "v2", credentials=creds)
    except Exception:
        return None


def fetch_video_stats(channel_id: str, video_ids: list[str] | None = None) -> list[dict]:
    """
    Fetch view count, likes, comments, and duration for videos.

    If video_ids is None, pulls stats for all uploaded videos in the calendar.
    Returns a list of dicts with: video_id, title, views, likes, comments,
    published_at, duration_s.
    """
    if not video_ids:
        cal = load_calendar()
        video_ids = [
            s["youtube_video_id"]
            for s in cal["slots"]
            if s["channel_id"] == channel_id
            and s.get("youtube_video_id")
        ]

    if not video_ids:
        return []

    yt = _get_youtube_service(channel_id)
    results = []

    # YouTube API accepts max 50 IDs per request
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()

        for item in resp.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            duration_str = item.get("contentDetails", {}).get("duration", "PT0S")

            results.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "duration": duration_str,
            })

    results.sort(key=lambda x: x["views"], reverse=True)
    return results


def fetch_deep_analytics(channel_id: str, days: int = 28) -> dict | None:
    """
    Fetch deep analytics from the YouTube Analytics API v2.
    Returns watch time, avg view duration, impressions, CTR,
    traffic sources, and per-video breakdowns.

    Returns None if analytics scope is unavailable.
    """
    yt_analytics = _get_analytics_service(channel_id)
    if not yt_analytics:
        return None

    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    result = {}

    # Channel-level overview
    try:
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=(
                "views,estimatedMinutesWatched,averageViewDuration,"
                "likes,subscribersGained,subscribersLost"
            ),
        ).execute()
        if resp.get("rows"):
            row = resp["rows"][0]
            result["channel_overview"] = {
                "period": f"{start_date} to {end_date}",
                "views": row[0],
                "watch_time_minutes": round(row[1], 1),
                "avg_view_duration_s": row[2],
                "likes": row[3],
                "subscribers_gained": row[4],
                "subscribers_lost": row[5],
                "net_subscribers": row[4] - row[5],
            }
    except Exception as e:
        result["channel_overview_error"] = str(e)

    # Per-video breakdown (top 25 by watch time)
    try:
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            dimensions="video",
            metrics=(
                "views,estimatedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,subscribersGained"
            ),
            maxResults=25,
            sort="-estimatedMinutesWatched",
        ).execute()
        videos = []
        for row in resp.get("rows", []):
            videos.append({
                "video_id": row[0],
                "views": row[1],
                "watch_time_min": round(row[2], 1),
                "avg_view_duration_s": row[3],
                "avg_view_percentage": round(row[4], 1),
                "subscribers_gained": row[5],
            })
        result["per_video"] = videos
    except Exception as e:
        result["per_video_error"] = str(e)

    # Traffic sources
    try:
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            dimensions="insightTrafficSourceType",
            metrics="views,estimatedMinutesWatched",
            sort="-views",
        ).execute()
        sources = []
        for row in resp.get("rows", []):
            sources.append({
                "source": row[0],
                "views": row[1],
                "watch_time_min": round(row[2], 1),
            })
        result["traffic_sources"] = sources
    except Exception as e:
        result["traffic_sources_error"] = str(e)

    return result if result else None


def _get_scheduled_times(channel_id: str) -> dict[str, str]:
    """Map video_id -> scheduled_time from the calendar for a channel."""
    cal = load_calendar()
    result = {}
    for s in cal["slots"]:
        if s["channel_id"] == channel_id and s.get("youtube_video_id"):
            result[s["youtube_video_id"]] = s.get("scheduled_time", "")
    return result


def _is_published(scheduled_time: str) -> bool:
    """Check if a scheduled_time is in the past (video is live)."""
    if not scheduled_time:
        return True  # no scheduled time means it was published immediately
    try:
        from datetime import timezone
        sched = datetime.fromisoformat(scheduled_time)
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= sched
    except (ValueError, TypeError):
        return True


def build_metrics_summary(channel_id: str) -> str | None:
    """
    Build a comprehensive metrics summary combining Data API stats
    and Analytics API deep metrics.

    Separates published videos (actually live) from scheduled videos
    (uploaded but not yet released) so the agent doesn't confuse
    "hasn't aired yet" with "flopped."

    Returns None if no videos have been uploaded yet.
    """
    stats = fetch_video_stats(channel_id)
    if not stats:
        return None

    schedule_map = _get_scheduled_times(channel_id)

    published = []
    scheduled = []
    for v in stats:
        sched_time = schedule_map.get(v["video_id"], "")
        if _is_published(sched_time):
            published.append(v)
        else:
            v["scheduled_time"] = sched_time
            scheduled.append(v)

    lines = [
        f"Total videos uploaded: {len(stats)}",
        f"  Published (live): {len(published)}",
        f"  Scheduled (not yet released): {len(scheduled)}",
    ]

    # Deep analytics (if available)
    deep = fetch_deep_analytics(channel_id)
    if deep and deep.get("channel_overview"):
        ov = deep["channel_overview"]
        lines.append(f"\n## Channel Overview (last 28 days)")
        lines.append(f"  Views: {ov['views']:,}")
        lines.append(f"  Watch time: {ov['watch_time_minutes']:,.0f} minutes")
        lines.append(f"  Avg view duration: {ov['avg_view_duration_s']}s")
        lines.append(f"  Subscribers gained: +{ov['subscribers_gained']}")
        lines.append(f"  Subscribers lost: -{ov['subscribers_lost']}")
        lines.append(f"  Net subscriber change: {ov['net_subscribers']:+d}")

    if deep and deep.get("traffic_sources"):
        lines.append(f"\n## Traffic Sources (where viewers come from)")
        for src in deep["traffic_sources"][:8]:
            lines.append(
                f"  {src['source']}: {src['views']:,} views, "
                f"{src['watch_time_min']:,.0f} min watch time"
            )

    if published:
        total_views = sum(v["views"] for v in published)
        avg_views = total_views / len(published) if published else 0
        lines.append(f"\n## Published Video Stats")
        lines.append(f"  Total views: {total_views:,}")
        lines.append(f"  Average views per video: {avg_views:,.0f}")

        # Merge deep per-video data if available
        deep_by_id = {}
        if deep and deep.get("per_video"):
            deep_by_id = {v["video_id"]: v for v in deep["per_video"]}

        top_5 = published[:5]
        lines.append("\n  Top performers:")
        for v in top_5:
            dv = deep_by_id.get(v["video_id"], {})
            retention = dv.get("avg_view_percentage")
            avg_dur = dv.get("avg_view_duration_s")
            subs = dv.get("subscribers_gained")

            base = (
                f"    - \"{v['title']}\" — {v['views']:,} views, "
                f"{v['likes']} likes, {v['comments']} comments"
            )
            extras = []
            if retention is not None:
                extras.append(f"{retention}% retention")
            if avg_dur is not None:
                extras.append(f"{avg_dur}s avg watch")
            if subs:
                extras.append(f"+{subs} subs")
            if extras:
                base += f" ({', '.join(extras)})"
            lines.append(base)

        bottom_5 = published[-5:] if len(published) > 5 else []
        if bottom_5:
            lines.append("\n  Lowest performers (among published):")
            for v in bottom_5:
                dv = deep_by_id.get(v["video_id"], {})
                retention = dv.get("avg_view_percentage")
                base = (
                    f"    - \"{v['title']}\" — {v['views']:,} views, "
                    f"{v['likes']} likes"
                )
                if retention is not None:
                    base += f" ({retention}% retention)"
                lines.append(base)

    if scheduled:
        lines.append(f"\nScheduled (NOT YET LIVE — do not judge performance):")
        for v in scheduled:
            lines.append(
                f"  - \"{v['title']}\" — releases {v.get('scheduled_time', 'unknown')}"
            )

    if not deep:
        lines.append(
            "\nNOTE: Deep analytics (watch time, retention, CTR, traffic) "
            "unavailable. Token needs yt-analytics.readonly scope. "
            "Re-authenticate to unlock."
        )

    return "\n".join(lines)


def save_metrics_snapshot(channel_id: str) -> Path:
    """
    Save current metrics and append to history for trend tracking.
    Each call adds a timestamped entry to metrics_history.json so
    the agent can detect performance trends over time.

    Also backfills video performance into the journal so the strategist
    can see how past strategy-linked videos actually performed.
    """
    stats = fetch_video_stats(channel_id)
    summary = build_metrics_summary(channel_id)

    # Backfill journal + training dataset + experiments with latest performance data
    try:
        from src.agent.journal import update_video_performance
        from src.agent.dataset_builder import link_outcomes_batch
        from src.agent.experiment_engine import update_experiment_metrics, evaluate_all, apply_confirmed_findings
        deep = fetch_deep_analytics(channel_id)
        deep_by_id = {}
        if deep and deep.get("per_video"):
            deep_by_id = {v["video_id"]: v for v in deep["per_video"]}

        batch_metrics = {}
        for v in stats:
            deep_data = deep_by_id.get(v["video_id"], {})
            perf = {
                "views": v["views"],
                "likes": v["likes"],
                "comments": v["comments"],
                "avg_view_percentage": deep_data.get("avg_view_percentage"),
                "avg_view_duration_s": deep_data.get("avg_view_duration_s"),
                "subscribers_gained": deep_data.get("subscribers_gained"),
            }
            update_video_performance(channel_id, v["video_id"], perf)
            update_experiment_metrics(channel_id, v["video_id"], perf)
            batch_metrics[v["video_id"]] = perf

        # Single file rewrite for all dataset records (not N rewrites)
        link_outcomes_batch(batch_metrics)

        # Evaluate experiments and auto-apply confirmed findings
        results = evaluate_all(channel_id)
        if results:
            apply_confirmed_findings(channel_id)
    except Exception:
        pass  # backfill is best-effort

    snapshot = {
        "channel_id": channel_id,
        "fetched_at": datetime.now().isoformat(),
        "summary": summary,
        "videos": stats,
    }

    # Latest snapshot (overwritten each time)
    latest_path = CHANNELS_DIR / channel_id / "metrics_latest.json"
    latest_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    # Historical log (appended — one entry per fetch)
    history_path = CHANNELS_DIR / channel_id / "metrics_history.json"
    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []

    history.append({
        "fetched_at": snapshot["fetched_at"],
        "total_videos": len(stats),
        "total_views": sum(v["views"] for v in stats),
        "avg_views": sum(v["views"] for v in stats) / len(stats) if stats else 0,
        "total_likes": sum(v["likes"] for v in stats),
        "per_video": [
            {"id": v["video_id"], "title": v["title"], "views": v["views"],
             "likes": v["likes"], "comments": v["comments"]}
            for v in stats
        ],
    })
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    print(f"[Analytics] Saved metrics for {channel_id} ({len(stats)} videos, "
          f"{len(history)} historical snapshots)")
    return latest_path


def detect_trends(channel_id: str) -> str | None:
    """
    Analyze metrics_history.json to detect performance trends.
    Uses the LLM to interpret the data — not just compare numbers,
    but reason about WHY things are changing and what it means.
    Falls back to basic math if the LLM call fails.
    """
    history_path = CHANNELS_DIR / channel_id / "metrics_history.json"
    if not history_path.exists():
        return None

    history = json.loads(history_path.read_text(encoding="utf-8"))
    if len(history) < 2:
        return None

    # Build raw data summary for the LLM
    recent_snapshots = history[-5:]
    data_lines = []
    for snap in recent_snapshots:
        data_lines.append(
            f"[{snap['fetched_at'][:10]}] "
            f"{snap['total_videos']} videos, "
            f"{snap['total_views']:,} total views, "
            f"{snap['avg_views']:.0f} avg/video, "
            f"{snap['total_likes']} likes"
        )
        if snap.get("per_video"):
            top_3 = sorted(snap["per_video"], key=lambda v: v["views"], reverse=True)[:3]
            for v in top_3:
                data_lines.append(f"    \"{v['title']}\" — {v['views']} views")

    # Per-video growth between last two snapshots
    recent = history[-1]
    prev = history[-2]
    prev_by_id = {v["id"]: v for v in prev.get("per_video", [])}
    growth_lines = []
    for v in recent.get("per_video", []):
        old = prev_by_id.get(v["id"])
        if old:
            growth = v["views"] - old["views"]
            if growth > 0:
                growth_lines.append(f"  +{growth:,} views: \"{v['title']}\"")
    growth_lines.sort(reverse=True)

    if growth_lines:
        data_lines.append("\nView growth since last snapshot:")
        data_lines.extend(growth_lines[:8])

    # Ask the LLM to interpret the trends
    try:
        from src.utils.llm import chat

        trend_prompt = (
            "You are analyzing YouTube channel performance data. "
            "Given the historical metrics snapshots below, provide a concise trend analysis.\n\n"
            "Focus on:\n"
            "1. Overall trajectory (growing, declining, stagnant, volatile?)\n"
            "2. Which specific videos are driving growth or dragging down averages?\n"
            "3. Any patterns in what's working (topic, title style, timing)?\n"
            "4. Actionable insight — what should be done differently?\n\n"
            "Be concise (4-6 sentences). Use specific numbers. "
            "Don't hedge — commit to an assessment.\n\n"
            f"## Historical Metrics\n\n" + "\n".join(data_lines)
        )

        analysis = chat(
            "You are a YouTube analytics expert. Be direct, data-driven, and actionable.",
            trend_prompt,
            temperature=1.0,
        )

        return f"## Performance Trends (LLM analysis)\n\n{analysis}"

    except Exception:
        # Fallback to basic math if LLM fails
        views_delta = recent["total_views"] - prev["total_views"]
        avg_delta = recent["avg_views"] - prev["avg_views"]
        lines = [
            "## Performance Trends\n",
            f"Snapshots: {prev['fetched_at'][:10]} -> {recent['fetched_at'][:10]}",
            f"View change: {views_delta:+,} total, {avg_delta:+,.0f} avg/video",
        ]
        if growth_lines:
            lines.append("\nFastest growing:")
            lines.extend(growth_lines[:5])
        return "\n".join(lines)
