"""
Stage 4a — Timeline Assembly (AI → Edit Decision List)

An LLM receives the enriched segments (with time ranges and footage metadata)
and outputs a structured JSON Edit Decision List (EDL) that tells the
video assembler exactly how to build the video.

For large segment counts (30+), segments are processed in batches to avoid
LLM output token limits and timeouts. Minimal context is passed between
batches to maintain transition continuity.
"""

import json
import time

from src.llm import chat_json

# Maximum segments per LLM call — keeps output well within token limits
_BATCH_SIZE = 25

SYSTEM_PROMPT = """\
You are a professional video editor AI. You are given a list of script segments, \
each with:
- slot_start / slot_end / slot_duration: the FULL time slot this clip must fill \
  (includes the narration AND any silence before the next segment begins)
- audio_start / audio_end: when the narration is actually being spoken within the slot
- footage_path: path to the downloaded stock footage clip
- footage_duration: total duration of the source clip in seconds
- visual_description: what this segment should look like

Your job is to produce an Edit Decision List (EDL) — a JSON array that tells \
a video rendering engine exactly how to assemble the final video.

For each entry in the EDL:
- "segment_id": which script segment this corresponds to
- "slot_start": start of this clip's time slot (seconds)
- "slot_end": end of this clip's time slot (seconds)
- "footage_file": path to the footage clip file
- "footage_trim_start": where to start in the source clip (seconds) — pick the \
  most visually interesting portion that fits the duration needed
- "footage_trim_end": where to stop in the source clip (seconds)
- "transition": "cut" or "crossfade"
- "transition_duration": duration of transition in seconds (0 for cut, 0.3-0.5 for crossfade)

Rules:
1. The footage trim duration (footage_trim_end - footage_trim_start) MUST equal \
   slot_duration (NOT audio_end - audio_start). The clip must fill the FULL time \
   slot including any silence after the narration, so the footage continues playing \
   naturally during pauses.
2. footage_trim_start must be >= 0 and footage_trim_end must be <= footage_duration.
3. If a clip is shorter than needed, set footage_trim_start to 0 and footage_trim_end \
   to the clip's full duration. The renderer will handle speed adjustment.
4. Prefer hard cuts ("cut") for most transitions. Use "crossfade" when the mood \
   shifts significantly between adjacent segments.
5. If footage_path is null (no footage found), set footage_file to null — the \
   renderer will use a black frame or the previous clip as fallback.

Respond ONLY with a valid JSON array. No extra text.\
"""


def _summarize_segment(seg: dict) -> dict:
    """Build a clean summary dict for the LLM (omit unnecessary fields)."""
    return {
        "segment_id": seg["segment_id"],
        "text": seg["text"],
        "visual_description": seg["visual_description"],
        "mood": seg.get("mood", "neutral"),
        "audio_start": seg["audio_start"],
        "audio_end": seg["audio_end"],
        "slot_start": seg["slot_start"],
        "slot_end": seg["slot_end"],
        "slot_duration": seg["slot_duration"],
        "footage_path": seg.get("footage_path"),
        "footage_duration": seg.get("footage_duration", 0),
    }


def _validate_edl(edl: list[dict], segments: list[dict]) -> list[dict]:
    """
    Validate and fix EDL entries: clamp trim points, enforce slot durations,
    and ensure slot info is attached for the assembler.
    """
    for entry in edl:
        if entry.get("footage_file") is None:
            continue

        # Find the matching segment to get slot_duration and footage_duration
        matching_seg = next(
            (s for s in segments if s["segment_id"] == entry["segment_id"]),
            None,
        )

        # The target duration is the SLOT duration, not the speech duration
        slot_dur = matching_seg["slot_duration"] if matching_seg else (
            entry.get("slot_end", entry.get("audio_end", 0))
            - entry.get("slot_start", entry.get("audio_start", 0))
        )

        # Clamp trim points to valid range
        entry["footage_trim_start"] = max(0.0, entry.get("footage_trim_start", 0))

        if matching_seg and matching_seg.get("footage_duration"):
            max_dur = matching_seg["footage_duration"]
            entry["footage_trim_end"] = min(
                entry.get("footage_trim_end", max_dur), max_dur
            )

        # Ensure trim duration matches the full slot duration
        actual_trim = entry.get("footage_trim_end", 0) - entry.get("footage_trim_start", 0)
        if abs(actual_trim - slot_dur) > 0.1:
            desired_end = entry["footage_trim_start"] + slot_dur
            if matching_seg and matching_seg.get("footage_duration"):
                if desired_end <= matching_seg["footage_duration"]:
                    entry["footage_trim_end"] = round(desired_end, 3)
                else:
                    entry["_needs_speed_adjust"] = True

        # Store slot info on the EDL entry for the assembler
        entry["slot_start"] = (
            matching_seg["slot_start"] if matching_seg
            else entry.get("slot_start", 0)
        )
        entry["slot_end"] = (
            matching_seg["slot_end"] if matching_seg
            else entry.get("slot_end", 0)
        )
        entry["slot_duration"] = round(slot_dur, 3)

    return edl


def _build_batch(
    batch_segments: list[dict],
    prev_mood: str | None = None,
    prev_transition: str | None = None,
) -> list[dict]:
    """
    Send a batch of segments to the LLM and return the partial EDL.
    Optionally includes context from the previous batch's last entry.
    """
    summaries = [_summarize_segment(seg) for seg in batch_segments]

    # Add context note if continuing from a previous batch
    context_note = ""
    if prev_mood or prev_transition:
        context_note = (
            f"\n\nContext from previous batch: the last segment had "
            f"mood=\"{prev_mood or 'neutral'}\" and used "
            f"transition=\"{prev_transition or 'cut'}\". "
            f"Maintain visual continuity.\n"
        )

    user_prompt = context_note + json.dumps(summaries, indent=2)

    edl = chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    if not isinstance(edl, list):
        raise ValueError("LLM returned invalid EDL (not a list).")

    return edl


def build_timeline(segments: list[dict]) -> list[dict]:
    """
    Use an LLM to generate an Edit Decision List from enriched segments.

    For small segment counts (<= _BATCH_SIZE), sends all at once.
    For larger counts, processes in batches with inter-batch context
    to maintain transition continuity.

    Returns a list of EDL entries (dicts).
    """
    total = len(segments)

    if total <= _BATCH_SIZE:
        # Single batch — existing behavior
        print(f"[Timeline Builder] Processing {total} segments in one batch...")
        edl = _build_batch(segments)
    else:
        # Batched processing
        num_batches = (total + _BATCH_SIZE - 1) // _BATCH_SIZE
        print(
            f"[Timeline Builder] Processing {total} segments in "
            f"{num_batches} batches of ~{_BATCH_SIZE}..."
        )

        edl = []
        prev_mood = None
        prev_transition = None

        for batch_idx in range(num_batches):
            start = batch_idx * _BATCH_SIZE
            end = min(start + _BATCH_SIZE, total)
            batch = segments[start:end]

            print(
                f"[Timeline Builder] Batch {batch_idx + 1}/{num_batches} "
                f"(segments {start + 1}–{end})..."
            )

            partial_edl = _build_batch(batch, prev_mood, prev_transition)
            edl.extend(partial_edl)

            # Capture context for the next batch
            if partial_edl:
                last_entry = partial_edl[-1]
                last_seg = batch[-1]
                prev_mood = last_seg.get("mood", "neutral")
                prev_transition = last_entry.get("transition", "cut")

            # Small delay between batches to respect LLM rate limits
            if batch_idx < num_batches - 1:
                time.sleep(2)

    if not edl:
        raise ValueError("LLM returned an empty EDL.")

    # Validate and fix all entries
    edl = _validate_edl(edl, segments)

    print(f"[Timeline Builder] Generated EDL with {len(edl)} entries.")
    return edl
