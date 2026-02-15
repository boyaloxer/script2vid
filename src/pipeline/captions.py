"""
Closed Captions Generator — SRT subtitle file from ElevenLabs alignment data

Takes the character-level timing data produced by Stage 3 (Voiceover) and
groups it into readable subtitle cues.  Each cue shows ~6-10 words at a time,
timed precisely to the narrator's speech.

The SRT file is burned into the video by FFmpeg's `subtitles` filter during
the final rendering pass.
"""

from pathlib import Path


def _format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds % 1) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _build_words(alignment: dict) -> list[dict]:
    """
    Reconstruct words from character-level alignment data.

    Returns a list of dicts:
        {"word": "Hello", "start": 0.0, "end": 0.35}
    """
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])

    n = min(len(chars), len(starts), len(ends))
    if n == 0:
        return []

    words: list[dict] = []
    current_word = ""
    word_start = None

    for i in range(n):
        ch = chars[i]

        # Space or newline = word boundary
        if ch in (" ", "\n", "\r"):
            if current_word:
                words.append({
                    "word": current_word,
                    "start": word_start,
                    "end": ends[i - 1] if i > 0 else starts[i],
                })
                current_word = ""
                word_start = None
            continue

        if word_start is None:
            word_start = starts[i]
        current_word += ch

    # Flush last word
    if current_word and word_start is not None:
        words.append({
            "word": current_word,
            "start": word_start,
            "end": ends[n - 1],
        })

    return words


def generate_srt(
    alignment: dict,
    output_path: Path,
    words_per_cue: int = 8,
) -> Path:
    """
    Generate an SRT subtitle file from ElevenLabs alignment data.

    Groups words into cues of approximately `words_per_cue` words each,
    breaking at sentence boundaries (periods, question marks, exclamation
    marks) when possible for natural reading.

    Args:
        alignment: The alignment dict with characters, start_times, end_times.
        output_path: Where to save the .srt file.
        words_per_cue: Target number of words per subtitle cue.

    Returns:
        Path to the generated SRT file.
    """
    words = _build_words(alignment)
    if not words:
        # Empty alignment — write an empty SRT
        output_path.write_text("", encoding="utf-8")
        return output_path

    cues: list[dict] = []
    cue_words: list[str] = []
    cue_start = words[0]["start"]

    sentence_enders = {".", "?", "!", ":", ";"}

    for i, w in enumerate(words):
        cue_words.append(w["word"])

        # Decide whether to close this cue
        at_sentence_end = w["word"] and w["word"][-1] in sentence_enders
        at_target_length = len(cue_words) >= words_per_cue
        is_last = i == len(words) - 1

        # Close cue at sentence boundaries once we have enough words,
        # or when we hit the target length, or at the end
        should_close = is_last or (at_sentence_end and len(cue_words) >= 4) or at_target_length

        if should_close:
            cues.append({
                "start": cue_start,
                "end": w["end"],
                "text": " ".join(cue_words),
            })
            cue_words = []
            # Next cue starts at the next word
            if i + 1 < len(words):
                cue_start = words[i + 1]["start"]

    # Build SRT content
    srt_lines: list[str] = []
    for idx, cue in enumerate(cues, 1):
        srt_lines.append(str(idx))
        srt_lines.append(
            f"{_format_srt_time(cue['start'])} --> {_format_srt_time(cue['end'])}"
        )
        srt_lines.append(cue["text"])
        srt_lines.append("")  # blank line between cues

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(srt_lines), encoding="utf-8")

    print(f"[Captions] Generated {len(cues)} subtitle cues -> {output_path}")
    return output_path
