"""
Trend Scout — Discover what's RISING in our niche right now.

This is NOT competitor analysis. We don't care what other channels are doing
or how many views they have — a popular creator's video getting views tells
us nothing about what WE should make.

Instead, this module answers: "What topics in our niche are people
increasingly interested in RIGHT NOW?"

It does this by:
  1. Generating niche-specific search queries from the content prompt
  2. Running TWO separate YouTube searches per query:
     - RECENT (last 3 days, sorted by date) — what's being posted NOW
     - RELEVANCE (sorted by relevance) — what YouTube thinks matters
  3. Comparing topic frequency across both to find RISING topics:
     topics appearing in recent results more than in relevance results
     are genuinely trending UP
  4. Asking the LLM to interpret the raw topic signals into content
     angles that fit OUR channel's voice and style

The output is NOT "copy this video" — it's "here are themes your audience
is thinking about right now, expressed in your channel's voice."
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import CHANNELS_DIR


def _get_youtube_service(channel_id: str):
    from src.agent.analytics import _get_youtube_service as _get_yt
    return _get_yt(channel_id)


def _extract_search_queries(channel_id: str) -> list[str]:
    """
    Generate search queries that reflect what our AUDIENCE would search for,
    not what competitors are making.
    """
    from src.utils.llm import chat_json
    from src.agent.script_generator import _load_content_prompt

    content_prompt = _load_content_prompt(channel_id)

    result = chat_json(
        "You help a YouTube channel discover trending topics in its niche.\n\n"
        "Given the channel's style guide, generate 6-10 search queries that "
        "the channel's TARGET AUDIENCE would type into YouTube right now.\n\n"
        "Think about:\n"
        "- What questions does this audience wonder about?\n"
        "- What feelings or experiences drive them to search?\n"
        "- What adjacent topics would they explore?\n"
        "- What current cultural moments connect to this niche?\n\n"
        "DO NOT generate queries about specific creators or channels.\n"
        "DO generate queries about TOPICS, FEELINGS, QUESTIONS.\n\n"
        "Respond with JSON: {\"queries\": [\"query 1\", \"query 2\", ...]}",
        f"## Channel Style Guide\n\n{content_prompt}",
        temperature=1.0,
    )

    return result.get("queries", [])


def _search_youtube(
    yt,
    query: str,
    order: str,
    published_after: str | None = None,
    max_results: int = 10,
    video_duration: str = "short",
) -> list[dict]:
    """Run a single YouTube search and return simplified results."""
    try:
        params = {
            "q": query,
            "type": "video",
            "part": "snippet",
            "order": order,
            "maxResults": max_results,
        }
        if video_duration:
            params["videoDuration"] = video_duration
        if published_after:
            params["publishedAfter"] = published_after

        resp = yt.search().list(**params).execute()

        results = []
        for item in resp.get("items", []):
            snippet = item.get("snippet", {})
            results.append({
                "video_id": item["id"]["videoId"],
                "title": snippet.get("title", ""),
                "channel_title": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt", ""),
                "description": snippet.get("description", "")[:150],
            })
        return results

    except Exception as e:
        print(f"[TrendScout] Search failed for '{query}' (order={order}): {e}")
        return []


def discover_rising_topics(
    channel_id: str,
    queries: list[str],
) -> dict:
    """
    For each query, compare RECENT results vs RELEVANCE results to find
    topics that are rising in frequency — the signal that something is
    trending.

    Returns structured data about what's rising, steady, and fading.
    """
    yt = _get_youtube_service(channel_id)
    three_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=3)
    ).isoformat()

    all_recent_titles = []
    all_relevance_titles = []
    query_signals = []

    for query in queries:
        # What's being posted RIGHT NOW
        recent = _search_youtube(
            yt, query, order="date",
            published_after=three_days_ago, max_results=10,
        )

        # What YouTube considers most relevant (mix of old and new)
        relevance = _search_youtube(
            yt, query, order="relevance",
            max_results=10,
        )

        recent_titles = [r["title"] for r in recent]
        relevance_titles = [r["title"] for r in relevance]

        all_recent_titles.extend(recent_titles)
        all_relevance_titles.extend(relevance_titles)

        query_signals.append({
            "query": query,
            "recent_count": len(recent),
            "recent_titles": recent_titles[:5],
            "relevance_titles": relevance_titles[:5],
        })

    return {
        "query_signals": query_signals,
        "total_recent": len(all_recent_titles),
        "total_relevance": len(all_relevance_titles),
    }


def analyze_rising_topics(
    channel_id: str,
    discovery: dict,
    own_metrics_summary: str | None = None,
) -> dict:
    """
    Ask the LLM to interpret the rising topic signals and suggest content
    angles that fit OUR channel's voice — not copies of what others made.
    """
    from src.utils.llm import chat_json
    from src.agent.script_generator import _load_content_prompt

    content_prompt = _load_content_prompt(channel_id)

    signals = discovery.get("query_signals", [])
    if not signals:
        return {
            "analysis": "No search data available.",
            "rising_themes": [],
            "content_angles": [],
        }

    signal_lines = []
    for s in signals:
        signal_lines.append(f"\nQuery: \"{s['query']}\" ({s['recent_count']} new videos in last 3 days)")
        if s["recent_titles"]:
            signal_lines.append("  Recent titles (last 3 days):")
            for t in s["recent_titles"]:
                signal_lines.append(f"    - {t}")
        if s["relevance_titles"]:
            signal_lines.append("  Top relevance titles (established):")
            for t in s["relevance_titles"]:
                signal_lines.append(f"    - {t}")

    # Extract just the voice/identity section for efficiency
    voice_section = content_prompt
    if "## Channel Identity" in content_prompt:
        start = content_prompt.index("## Channel Identity")
        end = content_prompt.find("\n## Content Format", start)
        if end > 0:
            voice_section = content_prompt[start:end]
        else:
            voice_section = content_prompt[:500]

    context_parts = [
        f"## Our Channel\n\n{voice_section}\n\n",
        "## Topic Signals from YouTube Search\n\n",
        "\n".join(signal_lines),
    ]

    if own_metrics_summary:
        context_parts.append(f"\n\n## Our Recent Performance\n\n{own_metrics_summary}")

    analysis = chat_json(
        "You are a content strategist for a specific YouTube channel. You've been "
        "given search data showing what's being posted and searched for in your "
        "niche RIGHT NOW.\n\n"
        "Your job is NOT to copy other creators. Your job is to identify THEMES "
        "and TOPICS that are rising in audience interest, then translate them "
        "into content angles that fit YOUR channel's unique voice and style.\n\n"
        "Analyze:\n"
        "1. RISING THEMES — Topics appearing frequently in recent (last 3 days) "
        "results. These are what people are actively searching for now.\n"
        "2. CONTENT ANGLES — For each rising theme, suggest how OUR channel "
        "would approach it. Not what others did — what WE would do in our voice.\n"
        "3. AUDIENCE MOOD — What do these search patterns tell us about what "
        "our audience is feeling/thinking about right now?\n"
        "4. IGNORE — Topics from the search results that don't fit our niche "
        "or are irrelevant noise.\n\n"
        "Be specific. Connect trending themes to our channel's identity.\n\n"
        "Respond with valid JSON:\n"
        "{\n"
        "  \"analysis\": \"2-3 sentences on what's rising in our niche right now\",\n"
        "  \"rising_themes\": [\n"
        "    {\"theme\": \"...\", \"signal\": \"why this appears to be trending\", "
        "\"relevance_to_us\": \"how it connects to our niche\"}\n"
        "  ],\n"
        "  \"content_angles\": [\n"
        "    {\"angle\": \"topic in our voice\", \"hook\": \"opening line or concept\", "
        "\"why_now\": \"why this is timely\"}\n"
        "  ],\n"
        "  \"audience_mood\": \"what people seem to be thinking about right now\",\n"
        "  \"ignore\": [\"irrelevant topic and why\"]\n"
        "}",
        "".join(context_parts),
        temperature=1.0,
    )

    return analysis


def scout_trends(channel_id: str, metrics_summary: str | None = None) -> dict:
    """
    Full trend scouting pipeline:
    1. Generate audience-centric search queries from the content prompt
    2. Discover rising topics by comparing recent vs relevance results
    3. Analyze through the lens of OUR channel's voice
    4. Save the intelligence report
    """
    print(f"[TrendScout] Discovering rising topics for {channel_id}...")

    queries = _extract_search_queries(channel_id)
    print(f"[TrendScout] Audience queries: {queries}")

    discovery = discover_rising_topics(channel_id, queries)
    print(
        f"[TrendScout] Scanned {discovery['total_recent']} recent + "
        f"{discovery['total_relevance']} relevance results"
    )

    analysis = analyze_rising_topics(channel_id, discovery, metrics_summary)

    report = {
        "channel_id": channel_id,
        "scouted_at": datetime.now(timezone.utc).isoformat(),
        "queries_used": queries,
        "discovery": discovery,
        "analysis": analysis,
    }

    report_path = CHANNELS_DIR / channel_id / "trend_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"[TrendScout] {analysis.get('analysis', 'N/A')}")
    if analysis.get("rising_themes"):
        print(f"[TrendScout] Rising themes: {len(analysis['rising_themes'])}")
        for t in analysis["rising_themes"][:3]:
            print(f"  - {t.get('theme', '?')}")
    if analysis.get("content_angles"):
        print(f"[TrendScout] Content angles: {len(analysis['content_angles'])}")
        for a in analysis["content_angles"][:3]:
            print(f"  - {a.get('angle', '?')}")

    return analysis


def get_trend_intelligence(channel_id: str) -> str | None:
    """
    Load the latest trend report and format it for the strategist/brain.
    Returns None if no report exists or it's too old (>7 days).
    """
    report_path = CHANNELS_DIR / channel_id / "trend_report.json"
    if not report_path.exists():
        return None

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    scouted_at = report.get("scouted_at", "")
    try:
        scouted = datetime.fromisoformat(scouted_at)
        if scouted.tzinfo is None:
            scouted = scouted.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - scouted).days > 7:
            return None
    except (ValueError, TypeError):
        pass

    analysis = report.get("analysis", {})
    if not analysis:
        return None

    lines = [f"## Trending Topics in Our Niche (scouted {scouted_at[:10]})\n"]

    if analysis.get("analysis"):
        lines.append(f"Overview: {analysis['analysis']}\n")

    if analysis.get("audience_mood"):
        lines.append(f"Audience mood: {analysis['audience_mood']}\n")

    if analysis.get("rising_themes"):
        lines.append("Rising themes:")
        for t in analysis["rising_themes"]:
            lines.append(
                f"  - {t.get('theme', '?')} — {t.get('relevance_to_us', '')}"
            )

    if analysis.get("content_angles"):
        lines.append("\nContent angles (in our voice):")
        for a in analysis["content_angles"]:
            lines.append(
                f"  - {a.get('angle', '?')} — hook: \"{a.get('hook', '')}\" "
                f"({a.get('why_now', '')})"
            )

    if analysis.get("ignore"):
        lines.append(f"\nIgnore (not for us): {', '.join(str(i) for i in analysis['ignore'][:3])}")

    return "\n".join(lines)
