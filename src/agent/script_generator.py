"""
Script Generator — LLM-powered content creation for channels.

Given a channel's content_prompt.md (style guide) and optional performance
data from past videos, generates a complete script + title + description
ready for the pipeline.
"""

import json
from pathlib import Path

from src.config import CHANNELS_DIR
from src.utils.llm import chat_json


_SYSTEM_PROMPT = """\
You are a content creator for a YouTube channel. You will be given the
channel's style guide (content prompt) and, when available, performance data
from recent videos.

Your job: generate ONE new video script that follows the style guide exactly,
along with a title and description.

RULES:
- Follow the content prompt's voice, tone, structure, and topic guidelines
  precisely. This is not a suggestion — it is a spec.
- The script must be ORIGINAL — do not rehash or closely rephrase any of the
  example scripts or past video scripts provided.
- If performance data is included, lean into topics/angles that performed well
  and avoid patterns that underperformed. But never sacrifice the channel's
  voice for engagement bait.
- Title and description must follow the guidelines in the content prompt.
- Include hashtags in the description as specified.

Respond with valid JSON only, no markdown fences:
{
  "script": "The full video script text...",
  "title": "The Video Title",
  "description": "The YouTube description with hashtags",
  "topic_reasoning": "1-2 sentences on why you chose this topic"
}
"""


def _load_content_prompt(channel_id: str) -> str:
    path = CHANNELS_DIR / channel_id / "content_prompt.md"
    if not path.exists():
        raise FileNotFoundError(
            f"No content_prompt.md found for channel '{channel_id}' at {path}"
        )
    return path.read_text(encoding="utf-8")


def _load_past_scripts(channel_id: str, limit: int = 10) -> list[str]:
    """Load titles of recently produced videos to avoid repetition."""
    from src.publishing.calendar_manager import load_calendar
    cal = load_calendar()
    titles = []
    for slot in reversed(cal["slots"]):
        if slot["channel_id"] != channel_id:
            continue
        if slot.get("title") and slot["status"] in ("assigned", "uploaded"):
            titles.append(slot["title"])
        if len(titles) >= limit:
            break
    return titles


def generate_script(
    channel_id: str,
    metrics_summary: str | None = None,
    topic_directive: str | None = None,
) -> dict:
    """
    Generate a new script for a channel.

    Args:
        channel_id: The channel to generate for (e.g. "deep_thoughts").
        metrics_summary: Optional human-readable summary of recent video
            performance (views, retention, top performers). Passed to the
            LLM to inform topic selection.
        topic_directive: Optional specific topic/angle from the strategist.
            When provided, the generator follows this direction instead of
            picking a topic freely.

    Returns:
        Dict with keys: script, title, description, topic_reasoning
    """
    content_prompt = _load_content_prompt(channel_id)
    past_titles = _load_past_scripts(channel_id)

    user_parts = [
        "## Channel Style Guide\n\n",
        content_prompt,
    ]

    if past_titles:
        user_parts.append("\n\n## Recently Published Videos (avoid repeating these)\n\n")
        for i, t in enumerate(past_titles, 1):
            user_parts.append(f"{i}. {t}\n")

    if metrics_summary:
        user_parts.append("\n\n## Recent Performance Data\n\n")
        user_parts.append(metrics_summary)
        user_parts.append(
            "\n\nUse this data to inform your topic choice. "
            "Lean into what works, avoid what doesn't."
        )

    if topic_directive:
        user_parts.append(f"\n\n## {topic_directive}")

    try:
        from src.agent.dataset_builder import get_past_generation_feedback
        gen_feedback = get_past_generation_feedback(channel_id)
        if gen_feedback:
            user_parts.append(f"\n\n{gen_feedback}")
    except Exception:
        pass

    user_parts.append(
        "\n\nNow generate ONE new, original video script with title "
        "and description. Respond with JSON only."
    )

    result = chat_json(_SYSTEM_PROMPT, "".join(user_parts), temperature=1.0)

    required = {"script", "title", "description"}
    missing = required - set(result.keys())
    if missing:
        raise ValueError(f"LLM response missing keys: {missing}")

    return result
