"""
Stage 4a — Timeline Assembly (AI → Edit Decision List)

An LLM receives the enriched segments (with time ranges and footage metadata)
and outputs a structured JSON Edit Decision List (EDL) that tells the
video assembler exactly how to build the video.
"""

from src.llm import chat_json

SYSTEM_PROMPT = """\
You are a professional video editor AI. You are given a list of script segments, \
each with:
- audio_start / audio_end: the time range in the narration audio
- footage_path: path to the downloaded stock footage clip
- footage_duration: total duration of the source clip in seconds
- visual_description: what this segment should look like

Your job is to produce an Edit Decision List (EDL) — a JSON array that tells \
a video rendering engine exactly how to assemble the final video.

For each entry in the EDL:
- "segment_id": which script segment this corresponds to
- "audio_start": start time in the narration audio (seconds)
- "audio_end": end time in the narration audio (seconds)
- "footage_file": path to the footage clip file
- "footage_trim_start": where to start in the source clip (seconds) — pick the \
  most visually interesting portion that fits the duration needed
- "footage_trim_end": where to stop in the source clip (seconds)
- "transition": "cut" or "crossfade"
- "transition_duration": duration of transition in seconds (0 for cut, 0.3-0.5 for crossfade)

Rules:
1. The footage trim duration (footage_trim_end - footage_trim_start) MUST equal \
   the audio segment duration (audio_end - audio_start). The video clip must exactly \
   fill its time slot.
2. footage_trim_start must be >= 0 and footage_trim_end must be <= footage_duration.
3. If a clip is shorter than needed, set footage_trim_start to 0 and footage_trim_end \
   to the clip's full duration. The renderer will handle speed adjustment.
4. Prefer hard cuts ("cut") for most transitions. Use "crossfade" when the mood \
   shifts significantly between adjacent segments.
5. If footage_path is null (no footage found), set footage_file to null — the \
   renderer will use a black frame or the previous clip as fallback.

Respond ONLY with a valid JSON array. No extra text.\
"""


def build_timeline(segments: list[dict]) -> list[dict]:
    """
    Use an LLM to generate an Edit Decision List from enriched segments.

    Each segment should already have:
        segment_id, text, visual_description, audio_start, audio_end,
        footage_path, footage_duration

    Returns a list of EDL entries (dicts).
    """
    # Build a clean summary for the LLM (don't send unnecessary fields)
    segment_summaries = []
    for seg in segments:
        segment_summaries.append({
            "segment_id": seg["segment_id"],
            "text": seg["text"],
            "visual_description": seg["visual_description"],
            "mood": seg.get("mood", "neutral"),
            "audio_start": seg["audio_start"],
            "audio_end": seg["audio_end"],
            "footage_path": seg.get("footage_path"),
            "footage_duration": seg.get("footage_duration", 0),
        })

    import json
    user_prompt = json.dumps(segment_summaries, indent=2)

    edl = chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    # Validate and fix basic issues
    if not isinstance(edl, list) or len(edl) == 0:
        raise ValueError("LLM returned an invalid or empty EDL.")

    for entry in edl:
        # Ensure trim duration matches audio duration
        audio_dur = entry["audio_end"] - entry["audio_start"]
        trim_dur = entry["footage_trim_end"] - entry["footage_trim_start"]

        if entry.get("footage_file") is None:
            continue

        # Clamp trim points to valid range
        entry["footage_trim_start"] = max(0.0, entry["footage_trim_start"])

        # Find the matching segment to get footage_duration
        matching_seg = next(
            (s for s in segments if s["segment_id"] == entry["segment_id"]),
            None,
        )
        if matching_seg and matching_seg.get("footage_duration"):
            max_dur = matching_seg["footage_duration"]
            entry["footage_trim_end"] = min(entry["footage_trim_end"], max_dur)

        # If trim is still mismatched, adjust end to match audio duration
        actual_trim = entry["footage_trim_end"] - entry["footage_trim_start"]
        if abs(actual_trim - audio_dur) > 0.1:
            # Try to extend the end point
            desired_end = entry["footage_trim_start"] + audio_dur
            if matching_seg and matching_seg.get("footage_duration"):
                if desired_end <= matching_seg["footage_duration"]:
                    entry["footage_trim_end"] = round(desired_end, 3)
                else:
                    # Clip is too short — mark for speed adjustment
                    entry["_needs_speed_adjust"] = True

    print(f"[Timeline Builder] Generated EDL with {len(edl)} entries.")
    return edl
