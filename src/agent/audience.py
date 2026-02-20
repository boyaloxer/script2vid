"""
Audience Intelligence — Mine YouTube comments for direct viewer feedback.

Comments are the richest qualitative signal available. Viewers literally tell
you what they want to see, what they loved, what confused them, and what they
hated. The agent currently ignores all of this.

This module:
  1. Fetches top-level comments from the channel's videos
  2. Groups them by video and sentiment
  3. Asks the LLM to extract actionable intelligence:
     - What topics viewers are requesting
     - What viewers loved (do more of)
     - What viewers criticized (avoid or fix)
     - Common questions (content gaps to fill)
     - Overall audience sentiment trajectory

The output feeds into the strategist and brain, giving the agent a direct
feedback channel from the people it's making content for.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import CHANNELS_DIR


def _get_youtube_service(channel_id: str):
    from src.agent.analytics import _get_youtube_service as _get_yt
    return _get_yt(channel_id)


def fetch_comments(
    channel_id: str,
    video_ids: list[str] | None = None,
    max_per_video: int = 50,
) -> dict[str, list[dict]]:
    """
    Fetch top-level comments for the channel's videos.

    Returns dict mapping video_id -> list of comment dicts.
    Each comment has: author, text, likes, published_at.
    """
    if not video_ids:
        from src.publishing.calendar_manager import load_calendar
        cal = load_calendar()
        video_ids = [
            s["youtube_video_id"]
            for s in cal["slots"]
            if s["channel_id"] == channel_id
            and s.get("youtube_video_id")
        ]

    if not video_ids:
        return {}

    yt = _get_youtube_service(channel_id)
    all_comments: dict[str, list[dict]] = {}

    for vid in video_ids:
        try:
            resp = yt.commentThreads().list(
                part="snippet",
                videoId=vid,
                maxResults=max_per_video,
                order="relevance",
                textFormat="plainText",
            ).execute()

            comments = []
            for item in resp.get("items", []):
                snippet = item["snippet"]["topLevelComment"]["snippet"]
                comments.append({
                    "author": snippet.get("authorDisplayName", ""),
                    "text": snippet.get("textDisplay", ""),
                    "likes": snippet.get("likeCount", 0),
                    "published_at": snippet.get("publishedAt", ""),
                })

            if comments:
                all_comments[vid] = comments

        except Exception as e:
            err_str = str(e)
            if "commentsDisabled" in err_str or "403" in err_str:
                continue
            print(f"[Audience] Failed to fetch comments for {vid}: {e}")
            continue

    return all_comments


def _build_comment_context(
    channel_id: str,
    comments_by_video: dict[str, list[dict]],
) -> str:
    """Format comments into a readable block for the LLM."""
    from src.agent.analytics import fetch_video_stats

    video_titles = {}
    try:
        stats = fetch_video_stats(channel_id, list(comments_by_video.keys()))
        video_titles = {s["video_id"]: s["title"] for s in stats}
    except Exception:
        pass

    lines = []
    total_comments = 0

    for vid, comments in comments_by_video.items():
        title = video_titles.get(vid, vid)
        lines.append(f"\n### \"{title}\" ({len(comments)} comments)")

        # Sort by likes (most-liked comments are most representative)
        for c in sorted(comments, key=lambda x: x["likes"], reverse=True)[:25]:
            likes_tag = f" [{c['likes']} likes]" if c["likes"] > 0 else ""
            lines.append(f"  - {c['text'][:300]}{likes_tag}")
            total_comments += 1

    return f"Total: {total_comments} comments across {len(comments_by_video)} videos\n" + "\n".join(lines)


def analyze_audience(channel_id: str) -> dict:
    """
    Full audience analysis pipeline:
    1. Fetch comments from all channel videos
    2. Send to LLM for structured analysis
    3. Save the report

    Returns the analysis dict.
    """
    from src.utils.llm import chat_json

    print(f"[Audience] Analyzing comments for {channel_id}...")

    comments_by_video = fetch_comments(channel_id)
    total = sum(len(v) for v in comments_by_video.values())
    print(f"[Audience] Fetched {total} comments from {len(comments_by_video)} videos")

    if total == 0:
        empty = {
            "analysis": "No comments available yet.",
            "requests": [],
            "loved": [],
            "criticized": [],
            "questions": [],
            "sentiment": "unknown",
        }
        _save_report(channel_id, empty, comments_by_video)
        return empty

    context = _build_comment_context(channel_id, comments_by_video)

    analysis = chat_json(
        "You are an audience analyst for a YouTube channel. Given the comments "
        "from the channel's videos, extract actionable intelligence.\n\n"
        "Analyze:\n"
        "1. REQUESTS — What topics/content are viewers explicitly asking for?\n"
        "2. LOVED — What specific things do viewers praise? (topics, style, "
        "specific lines, the voice, the visuals, the vibe)\n"
        "3. CRITICIZED — What do viewers complain about or dislike?\n"
        "4. QUESTIONS — What questions do viewers ask that could become content?\n"
        "5. SENTIMENT — Overall sentiment trajectory (positive/mixed/negative) "
        "and why.\n"
        "6. PATTERNS — Any recurring themes, phrases, or behaviors in the comments.\n\n"
        "Be specific. Quote actual comments when relevant. Don't generalize — "
        "name exact topics and feedback.\n\n"
        "Respond with valid JSON:\n"
        "{\n"
        "  \"analysis\": \"2-3 sentence overview of what the audience is saying\",\n"
        "  \"requests\": [{\"topic\": \"...\", \"evidence\": \"quoted comment or paraphrase\", \"frequency\": \"how many asked\"}],\n"
        "  \"loved\": [{\"what\": \"...\", \"evidence\": \"...\"}],\n"
        "  \"criticized\": [{\"what\": \"...\", \"evidence\": \"...\", \"severity\": \"minor|moderate|major\"}],\n"
        "  \"questions\": [{\"question\": \"...\", \"content_potential\": \"high|medium|low\"}],\n"
        "  \"sentiment\": \"positive|mixed|negative\",\n"
        "  \"patterns\": [\"pattern 1\", \"pattern 2\"]\n"
        "}",
        context,
        temperature=1.0,
    )

    _save_report(channel_id, analysis, comments_by_video)

    print(f"[Audience] Sentiment: {analysis.get('sentiment', '?')}")
    if analysis.get("requests"):
        print(f"[Audience] Requests: {len(analysis['requests'])}")
        for r in analysis["requests"][:3]:
            print(f"  - {r.get('topic', '?')}")
    if analysis.get("loved"):
        print(f"[Audience] Loved: {len(analysis['loved'])} things")
    if analysis.get("criticized"):
        print(f"[Audience] Criticized: {len(analysis['criticized'])} things")

    return analysis


def _save_report(
    channel_id: str,
    analysis: dict,
    comments_by_video: dict[str, list[dict]],
):
    report = {
        "channel_id": channel_id,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "videos_analyzed": len(comments_by_video),
        "total_comments": sum(len(v) for v in comments_by_video.values()),
        "analysis": analysis,
    }
    report_path = CHANNELS_DIR / channel_id / "audience_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_audience_intelligence(channel_id: str) -> str | None:
    """
    Load the latest audience report and format it for the strategist/brain.
    Returns None if no report exists or it's too old (>14 days).
    """
    report_path = CHANNELS_DIR / channel_id / "audience_report.json"
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
        age_days = (datetime.now(timezone.utc) - analyzed).days
        if age_days > 14:
            return None
    except (ValueError, TypeError):
        pass

    analysis = report.get("analysis", {})
    if not analysis or analysis.get("analysis") == "No comments available yet.":
        return None

    lines = [f"## Audience Intelligence (analyzed {analyzed_at[:10]})\n"]

    if analysis.get("analysis"):
        lines.append(f"Overview: {analysis['analysis']}\n")

    lines.append(f"Sentiment: {analysis.get('sentiment', 'unknown')}")

    if analysis.get("requests"):
        lines.append("\nViewer requests (topics they want to see):")
        for r in analysis["requests"]:
            lines.append(f"  - {r.get('topic', '?')} (evidence: \"{r.get('evidence', '')}\")")

    if analysis.get("loved"):
        lines.append("\nWhat viewers love:")
        for item in analysis["loved"]:
            lines.append(f"  - {item.get('what', '?')}")

    if analysis.get("criticized"):
        lines.append("\nCriticisms:")
        for item in analysis["criticized"]:
            lines.append(
                f"  - [{item.get('severity', '?')}] {item.get('what', '?')}"
            )

    if analysis.get("questions"):
        high = [q for q in analysis["questions"] if q.get("content_potential") == "high"]
        if high:
            lines.append("\nHigh-potential questions from viewers:")
            for q in high:
                lines.append(f"  - \"{q.get('question', '?')}\"")

    if analysis.get("patterns"):
        lines.append(f"\nBehavioral patterns: {', '.join(analysis['patterns'][:5])}")

    return "\n".join(lines)
