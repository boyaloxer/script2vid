"""
Agent Chat — lets users have a conversation with the agent from the dashboard.

The agent responds using the LLM with full awareness of the channel's state,
strategy, metrics, memory, and capabilities. This is distinct from the command
queue (which queues actions for the agent loop) — chat is for asking questions,
getting status updates, or discussing strategy in real time.
"""

import logging

from src.utils.llm import chat

log = logging.getLogger("agent.chat")

_SYSTEM_PROMPT = """\
You are the script2vid autonomous agent — an AI that manages YouTube channels.
You are currently having a conversation with your operator through the dashboard.

You have full awareness of:
- Channel performance metrics, strategies, and content plans
- Your own capabilities (trend scouting, audience analysis, script generation, etc.)
- Your memory (beliefs, past episodes, session history)
- API quotas and system health

When the operator asks questions, answer from your perspective AS the agent.
Be concise, direct, and helpful. Use specific numbers and data when available.
If they ask you to do something, explain what you'd do and suggest they use
the command input to queue it (commands like "check metrics", "make a video
about X", "scout trends", etc.).

Keep responses under 3 paragraphs. No markdown formatting — plain text only,
since this displays in a terminal-style feed.
"""

_MAX_CONTEXT_CHARS = 24_000


def _build_context(channel_id: str | None) -> str:
    """Gather world state + memory, truncated to fit the model's context."""
    parts = []

    try:
        from src.agent.observer import build_world_state, world_state_to_text
        channels = [channel_id] if channel_id else None
        state = build_world_state(channel_filter=channels)
        ws_text = world_state_to_text(state)
        parts.append(ws_text)
    except Exception as e:
        parts.append(f"(World state unavailable: {e})")

    if channel_id:
        try:
            from src.agent.memory import recall_for_brain
            mem = recall_for_brain(channel_id)
            if mem:
                parts.append(f"\n{mem}")
        except Exception:
            pass

    context = "\n".join(parts)
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS] + "\n\n... (context trimmed for length)"
    return context


def agent_reply(
    user_message: str,
    channel_id: str | None = None,
) -> str:
    """
    Get an LLM-powered response from the agent's perspective.
    Tries with full context first; falls back to minimal context on failure.
    """
    context = _build_context(channel_id)
    prompt = f"{context}\n\n## Operator Message\n\n{user_message}"

    try:
        return chat(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=1.0,
            max_tokens=1024,
        )
    except Exception as e:
        log.warning("Chat with full context failed (%s), retrying minimal", e)

    # Fallback: minimal context so the agent can still answer
    minimal = (
        f"You are managing channel: {channel_id or 'unknown'}.\n"
        f"(Full context unavailable due to API limits.)\n\n"
        f"## Operator Message\n\n{user_message}"
    )
    try:
        return chat(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=minimal,
            temperature=1.0,
            max_tokens=1024,
        )
    except Exception as e2:
        log.error("Chat fallback also failed: %s", e2)
        return f"I couldn't process that — the LLM returned an error: {e2}"
