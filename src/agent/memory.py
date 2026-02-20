"""
Persistent Memory — The agent's working memory that survives across sessions.

Unlike the journal (which records facts about what happened) or the strategy
(which is a content plan), memory stores the agent's own REASONING STATE:
  - What it was thinking about last time
  - Hypotheses it's formed but hasn't tested yet
  - Observations it wants to follow up on
  - Multi-step plans that span multiple sessions
  - Beliefs about what works (updated by evidence)

This is the difference between "stateless tool" and "agent that thinks."

Architecture inspired by:
  - MemRL: Episodic memory with environmental feedback for policy improvement
  - Memento-II: Stateful reflective decision process (write/read operations)
  - EverMemOS: Engram-inspired memory lifecycle (trace → consolidate → recall)

Memory has three layers:
  1. SCRATCHPAD — Short-term working memory. The brain writes what it's
     thinking at the end of each session. Overwritten each session.
  2. BELIEFS — Accumulated convictions about what works, updated when
     evidence confirms or contradicts them. Persistent across sessions.
  3. EPISODES — Key events the agent wants to remember. Capped and pruned
     by relevance. The agent's autobiography.

Storage: channels/<id>/agent_memory.json
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from src.config import CHANNELS_DIR

_MAX_EPISODES = 50
_MAX_BELIEFS = 30


def _memory_path(channel_id: str) -> Path:
    return CHANNELS_DIR / channel_id / "agent_memory.json"


def _load_memory(channel_id: str) -> dict:
    path = _memory_path(channel_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "scratchpad": None,
        "beliefs": [],
        "episodes": [],
    }


def _save_memory(channel_id: str, memory: dict):
    path = _memory_path(channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# SCRATCHPAD — What the brain was thinking at end of last session
# ─────────────────────────────────────────────────────────────────────

def save_scratchpad(channel_id: str, thoughts: str):
    """
    Save the brain's end-of-session reflection.
    Called at the end of each agent session so the brain can pick up
    where it left off next time.
    """
    memory = _load_memory(channel_id)
    memory["scratchpad"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "thoughts": thoughts,
    }
    _save_memory(channel_id, memory)


def get_scratchpad(channel_id: str) -> str | None:
    """Get the brain's last scratchpad entry."""
    memory = _load_memory(channel_id)
    sp = memory.get("scratchpad")
    if sp and sp.get("thoughts"):
        return sp["thoughts"]
    return None


# ─────────────────────────────────────────────────────────────────────
# BELIEFS — Accumulated convictions about what works
# ─────────────────────────────────────────────────────────────────────

def update_belief(
    channel_id: str,
    belief: str,
    confidence: str = "hypothesis",
    evidence: str | None = None,
):
    """
    Add or update a belief.

    Confidence levels:
      - "hypothesis" — Suspected but not tested
      - "observed"   — Seen in data but not statistically confirmed
      - "confirmed"  — Backed by experiment or strong repeated evidence
      - "disproven"  — Tested and found false

    If a belief with the same text already exists, updates its confidence
    and appends new evidence.
    """
    memory = _load_memory(channel_id)
    beliefs = memory.get("beliefs", [])

    existing = None
    for b in beliefs:
        if b["belief"].lower().strip() == belief.lower().strip():
            existing = b
            break

    if existing:
        existing["confidence"] = confidence
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        if evidence:
            existing.setdefault("evidence_history", [])
            existing["evidence_history"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "evidence": evidence,
            })
    else:
        entry = {
            "belief": belief,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_history": [],
        }
        if evidence:
            entry["evidence_history"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "evidence": evidence,
            })
        beliefs.append(entry)

    # Cap beliefs
    if len(beliefs) > _MAX_BELIEFS:
        # Remove oldest disproven ones first, then oldest hypotheses
        disproven = [b for b in beliefs if b["confidence"] == "disproven"]
        if disproven:
            beliefs.remove(disproven[0])
        else:
            hypotheses = [b for b in beliefs if b["confidence"] == "hypothesis"]
            if hypotheses:
                beliefs.remove(hypotheses[0])
            else:
                beliefs.pop(0)

    memory["beliefs"] = beliefs
    _save_memory(channel_id, memory)


def get_beliefs(channel_id: str) -> list[dict]:
    memory = _load_memory(channel_id)
    return memory.get("beliefs", [])


# ─────────────────────────────────────────────────────────────────────
# EPISODES — Key events the agent wants to remember
# ─────────────────────────────────────────────────────────────────────

def record_episode(channel_id: str, event: str, significance: str = "normal"):
    """
    Record a notable event in the agent's episodic memory.

    Significance:
      - "minor"  — Routine event, pruned first
      - "normal" — Standard event
      - "major"  — Important turning point, kept longest
    """
    memory = _load_memory(channel_id)
    episodes = memory.get("episodes", [])

    episodes.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "significance": significance,
    })

    # Prune if over cap — remove minor events first, then oldest normal ones
    while len(episodes) > _MAX_EPISODES:
        minor = [e for e in episodes if e["significance"] == "minor"]
        if minor:
            episodes.remove(minor[0])
        else:
            normal = [e for e in episodes if e["significance"] == "normal"]
            if normal:
                episodes.remove(normal[0])
            else:
                episodes.pop(0)

    memory["episodes"] = episodes
    _save_memory(channel_id, memory)


def get_episodes(channel_id: str, limit: int = 20) -> list[dict]:
    memory = _load_memory(channel_id)
    episodes = memory.get("episodes", [])
    return episodes[-limit:]


# ─────────────────────────────────────────────────────────────────────
# RECALL — Build context for the brain from persistent memory
# ─────────────────────────────────────────────────────────────────────

def recall_for_brain(channel_id: str) -> str | None:
    """
    Build a memory context block for the brain's prompt.
    Includes scratchpad, active beliefs, and recent episodes.
    Returns None if no memory exists yet.
    """
    memory = _load_memory(channel_id)

    has_content = (
        memory.get("scratchpad")
        or memory.get("beliefs")
        or memory.get("episodes")
    )
    if not has_content:
        return None

    lines = ["## Your Memory (persistent across sessions)\n"]

    # Scratchpad — most recent thoughts
    sp = memory.get("scratchpad")
    if sp and sp.get("thoughts"):
        lines.append(f"### Last Session Thoughts ({sp['timestamp'][:10]})")
        lines.append(sp["thoughts"])
        lines.append("")

    # Beliefs — accumulated knowledge
    beliefs = memory.get("beliefs", [])
    active_beliefs = [b for b in beliefs if b["confidence"] != "disproven"]
    if active_beliefs:
        lines.append("### Current Beliefs")
        for b in active_beliefs:
            confidence_icon = {
                "hypothesis": "?",
                "observed": "~",
                "confirmed": "!",
            }.get(b["confidence"], "?")
            lines.append(f"  [{confidence_icon}] {b['belief']} ({b['confidence']})")
        lines.append("")

    # Disproven beliefs — things we know DON'T work
    disproven = [b for b in beliefs if b["confidence"] == "disproven"]
    if disproven:
        lines.append("### Disproven (don't repeat these mistakes)")
        for b in disproven:
            lines.append(f"  [X] {b['belief']}")
        lines.append("")

    # Recent episodes — key events
    episodes = memory.get("episodes", [])
    if episodes:
        recent = episodes[-10:]
        lines.append("### Recent Events")
        for ep in recent:
            sig = "*" if ep["significance"] == "major" else ""
            lines.append(f"  [{ep['timestamp'][:10]}] {sig}{ep['event']}")

    return "\n".join(lines) if len(lines) > 1 else None


# ─────────────────────────────────────────────────────────────────────
# REFLECT — End-of-session reflection that updates all memory layers
# ─────────────────────────────────────────────────────────────────────

def reflect_on_session(
    channel_id: str,
    session_log: list[str],
    slots_filled: int,
    world_state_text: str,
):
    """
    Called at the end of each agent session. Asks the LLM to reflect on
    what happened and update the agent's persistent memory.

    This is the core mechanism for cross-session learning:
    the agent looks at what just happened and decides what to remember.
    """
    from src.utils.llm import chat_json

    memory = _load_memory(channel_id)

    reflection_prompt = """\
You are an autonomous YouTube channel agent reflecting on a completed work session.
Review what happened and update your persistent memory.

Your memory has three parts:
1. SCRATCHPAD — Your current train of thought. What are you working on?
   What should you do next session? What were you in the middle of?
2. BELIEFS — Things you think are true about what works for this channel.
   Only add beliefs that are supported by evidence from this session.
   Update confidence: "hypothesis" → "observed" → "confirmed" (or "disproven")
3. EPISODES — Notable events worth remembering. Only record things that
   will be useful to your future self (not routine operations).

Respond with valid JSON:
{
  "scratchpad": "2-4 sentences about your current thinking and what to do next session",
  "new_beliefs": [
    {"belief": "...", "confidence": "hypothesis|observed|confirmed|disproven", "evidence": "why you think this"}
  ],
  "episodes": [
    {"event": "brief description of notable event", "significance": "minor|normal|major"}
  ]
}

Keep it concise. Only add beliefs and episodes that matter. An empty list is fine
if nothing notable happened.
"""

    context_parts = [f"## What Happened This Session\n\nSlots filled: {slots_filled}\n\n"]

    if session_log:
        context_parts.append("Session log:\n")
        for entry in session_log[-20:]:
            context_parts.append(f"- {entry}\n")

    # Include current memory so the agent can build on it
    existing_recall = recall_for_brain(channel_id)
    if existing_recall:
        context_parts.append(f"\n\n{existing_recall}")

    context_parts.append(f"\n\n## Current World State (at session end)\n\n{world_state_text}")

    try:
        result = chat_json(reflection_prompt, "".join(context_parts), temperature=1.0)
    except Exception:
        return  # reflection failure is non-fatal

    # Update scratchpad
    if result.get("scratchpad"):
        save_scratchpad(channel_id, result["scratchpad"])

    # Update beliefs
    for b in result.get("new_beliefs", []):
        if isinstance(b, dict) and b.get("belief"):
            update_belief(
                channel_id,
                belief=b["belief"],
                confidence=b.get("confidence", "hypothesis"),
                evidence=b.get("evidence"),
            )

    # Record episodes
    for ep in result.get("episodes", []):
        if isinstance(ep, dict) and ep.get("event"):
            record_episode(
                channel_id,
                event=ep["event"],
                significance=ep.get("significance", "normal"),
            )
