"""
Strategist — Deep content strategy planning.

Unlike the script generator which just writes the next script, the strategist
thinks at a higher level: what topics are working, what's underperforming,
what gaps exist, and what the next batch of content should focus on.

The output is a content plan that the script generator follows.
"""

import json
from pathlib import Path

from src.config import CHANNELS_DIR
from src.utils.llm import chat_json
from src.agent.analytics import detect_trends
from src.agent.script_generator import _load_content_prompt, _load_past_scripts
from src.agent.journal import get_learnings_summary, record_strategy_review


_STRATEGY_PROMPT = """\
You are a YouTube content strategist. You analyze channel performance data
and create actionable content plans.

Your job:
1. Look at what's working (high views/likes) and what's not
2. Identify PATTERNS — which topics, hooks, or structures perform best
3. Identify GAPS — themes from the style guide that haven't been explored
4. Consider what would actually work with STOCK FOOTAGE from Pexels
   (the videos are made using stock footage, so topics need to be visually
   representable with generic footage: people, nature, cities, objects, etc.)
5. If TRENDING TOPIC data is available, use it to stay timely:
   - RISING THEMES tell you what the audience is thinking about NOW
   - CONTENT ANGLES are pre-filtered for our voice — use them as inspiration
   - AUDIENCE MOOD helps you match the emotional register of the moment
   - Do NOT copy other creators — adapt trending themes to OUR unique style
6. If AUDIENCE INTELLIGENCE is available, prioritize it heavily:
   - Viewer REQUESTS are gold — they tell you exactly what to make
   - Double down on what viewers LOVE
   - Fix or avoid what viewers CRITICIZE
   - High-potential QUESTIONS from comments can become entire videos
7. Create a concrete content plan for the next batch of videos

IMPORTANT constraints:
- Videos use stock footage from Pexels, NOT custom footage. Topics must be
  visually representable with generic stock video (people walking, cityscapes,
  nature, close-ups of hands/faces, everyday objects, etc.)
- Avoid topics that require very specific visuals that stock footage can't
  provide (e.g., "the inside of your childhood home" — too specific)
- Great stock footage topics: universal human moments, nature metaphors,
  everyday objects/actions, crowds, solitude, time passing

Respond with valid JSON:
{
  "analysis": "2-3 sentence summary of what's working and what's not",
  "patterns": ["pattern 1", "pattern 2", ...],
  "gaps": ["underexplored theme 1", "underexplored theme 2", ...],
  "content_plan": [
    {
      "topic": "brief topic description",
      "angle": "the specific angle or hook",
      "why": "why this should perform well",
      "visual_notes": "what stock footage would work for this"
    }
  ],
  "avoid": ["topics to avoid and why"]
}
"""


def build_strategy(channel_id: str, metrics_summary: str | None = None) -> dict:
    """
    Build a content strategy for a channel based on performance data,
    content prompt, past scripts, and trend analysis.
    """
    content_prompt = _load_content_prompt(channel_id)
    past_titles = _load_past_scripts(channel_id)
    trends = detect_trends(channel_id)

    # Load any previous strategy for continuity
    strategy_path = CHANNELS_DIR / channel_id / "content_strategy.json"
    prev_strategy = None
    if strategy_path.exists():
        try:
            prev_strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    user_parts = []

    user_parts.append("## Channel Style Guide\n\n")
    user_parts.append(content_prompt)

    if metrics_summary:
        user_parts.append(f"\n\n## Performance Data\n\n{metrics_summary}")

    if trends:
        user_parts.append(f"\n\n{trends}")

    if past_titles:
        user_parts.append("\n\n## Recent Titles (already covered)\n\n")
        for t in past_titles:
            user_parts.append(f"- {t}\n")

    if prev_strategy:
        user_parts.append("\n\n## Previous Strategy\n\n")
        user_parts.append(f"Analysis: {prev_strategy.get('analysis', 'N/A')}\n")
        if prev_strategy.get("content_plan"):
            user_parts.append("Previous plan topics:\n")
            for item in prev_strategy["content_plan"]:
                user_parts.append(f"- {item.get('topic', 'N/A')}\n")

    # Inject long-term learnings from the performance journal
    learnings = get_learnings_summary(channel_id)
    if learnings:
        user_parts.append(f"\n\n{learnings}")

    # Inject competitive landscape intelligence from the trend scout
    try:
        from src.agent.trend_scout import get_trend_intelligence
        trend_intel = get_trend_intelligence(channel_id)
        if trend_intel:
            user_parts.append(f"\n\n{trend_intel}")
    except Exception:
        pass

    # Inject audience feedback from comment analysis
    try:
        from src.agent.audience import get_audience_intelligence
        audience_intel = get_audience_intelligence(channel_id)
        if audience_intel:
            user_parts.append(f"\n\n{audience_intel}")
    except Exception:
        pass

    # Inject scheduling intelligence
    try:
        from src.agent.scheduler import get_schedule_intelligence
        schedule_intel = get_schedule_intelligence(channel_id)
        if schedule_intel:
            user_parts.append(f"\n\n{schedule_intel}")
    except Exception:
        pass

    # Review the previous strategy before overwriting it
    if prev_strategy and metrics_summary:
        try:
            _review_outgoing_strategy(channel_id, prev_strategy, metrics_summary)
        except Exception:
            pass  # review is best-effort

    full_input = "".join(user_parts)
    result = chat_json(_STRATEGY_PROMPT, full_input, temperature=1.0)

    # Save the strategy
    result["channel_id"] = channel_id
    result["generated_at"] = __import__("datetime").datetime.now().isoformat()
    strategy_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Record for training dataset
    try:
        from src.agent.dataset_builder import record_strategy
        record_strategy(channel_id, full_input, result)
    except Exception:
        pass

    print(f"[Strategist] Strategy generated for {channel_id}")
    print(f"  Analysis: {result.get('analysis', 'N/A')}")
    if result.get("content_plan"):
        print(f"  Planned topics: {len(result['content_plan'])}")
        for item in result["content_plan"][:3]:
            print(f"    - {item.get('topic', '?')}")

    return result


def _review_outgoing_strategy(
    channel_id: str, prev_strategy: dict, metrics_summary: str
):
    """
    Before overwriting a strategy, grade it and save a reflection to the journal.
    This closes the feedback loop: strategy -> videos -> metrics -> review.
    """
    from src.agent.journal import _load_journal

    journal = _load_journal(channel_id)
    analysis = prev_strategy.get("analysis", "")
    plan_topics = [
        item.get("topic", "") for item in prev_strategy.get("content_plan", [])
    ]

    # Find journal entries that were produced under this strategy
    strategy_entries = [
        e for e in journal.get("entries", [])
        if e.get("strategy_analysis") == analysis and e.get("performance")
    ]

    if not strategy_entries:
        return

    views = [e["performance"]["views"] for e in strategy_entries]
    avg_views = sum(views) / len(views) if views else 0

    retentions = [
        e["performance"]["avg_view_percentage"]
        for e in strategy_entries
        if e["performance"].get("avg_view_percentage")
    ]
    avg_retention = sum(retentions) / len(retentions) if retentions else None

    retention_line = f"Avg retention: {avg_retention:.1f}%\n" if avg_retention else ""
    reflection_prompt = (
        f"You produced {len(strategy_entries)} videos under this strategy:\n"
        f"Strategy: {analysis}\n"
        f"Topics planned: {plan_topics}\n"
        f"Avg views: {avg_views:.0f}\n"
        f"{retention_line}"
        f"\nCurrent channel metrics:\n{metrics_summary}\n\n"
        f"In 1-2 sentences, what worked and what didn't? "
        f"Be specific about which topics or angles performed best."
    )

    from src.utils.llm import chat
    reflection = chat(
        "You are a YouTube content analyst reviewing a past strategy. Be concise.",
        reflection_prompt,
    )

    record_strategy_review(
        channel_id=channel_id,
        strategy_analysis=analysis,
        videos_produced=len(strategy_entries),
        avg_views=avg_views,
        avg_retention=avg_retention,
        reflection=reflection,
    )

    print(f"[Strategist] Reviewed outgoing strategy: {reflection[:120]}...")


def peek_next_topic(channel_id: str) -> dict | None:
    """
    Get the next unused topic WITHOUT marking it as used.
    Call consume_topic() only after the pipeline succeeds.
    """
    strategy_path = CHANNELS_DIR / channel_id / "content_strategy.json"
    if not strategy_path.exists():
        return None

    strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
    plan = strategy.get("content_plan", [])
    used = _load_used_topics(channel_id)

    for item in plan:
        if item.get("topic", "") not in used:
            return item
    return None


def consume_topic(channel_id: str, topic_key: str):
    """Mark a topic as used. Call only after successful pipeline completion."""
    used = _load_used_topics(channel_id)
    if topic_key not in used:
        used.append(topic_key)
        used_path = CHANNELS_DIR / channel_id / "used_topics.json"
        used_path.write_text(json.dumps(used, indent=2), encoding="utf-8")


def _load_used_topics(channel_id: str) -> list:
    used_path = CHANNELS_DIR / channel_id / "used_topics.json"
    if used_path.exists():
        try:
            return json.loads(used_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


# Backwards compat
def get_next_topic(channel_id: str) -> dict | None:
    """Deprecated — use peek_next_topic + consume_topic instead."""
    return peek_next_topic(channel_id)
