"""
Stage 3 — Voiceover Generation + Timestamp Extraction

Sends the script to ElevenLabs TTS with timestamps enabled.
Returns the audio file path and word-level timing data,
then maps timing back onto the script segments.
"""

import base64
from pathlib import Path
import requests

from src.config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_BASE_URL,
    ELEVENLABS_VOICE_ID,
    AUDIO_DIR,
)


def generate_voiceover(script_text: str) -> tuple[Path, dict]:
    """
    Generate narration audio with character-level timestamps.

    Returns:
        (audio_path, alignment_data)

        alignment_data has the structure:
        {
            "characters": ["H", "e", "l", "l", "o", ...],
            "character_start_times_seconds": [0.0, 0.05, ...],
            "character_end_times_seconds": [0.05, 0.12, ...]
        }
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set. Add it to your .env file.")

    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{ELEVENLABS_VOICE_ID}/with-timestamps"

    resp = requests.post(
        url,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": script_text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        },
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()

    # Decode and save audio
    audio_bytes = base64.b64decode(data["audio_base64"])
    audio_path = AUDIO_DIR / "narration.mp3"
    audio_path.write_bytes(audio_bytes)
    print(f"[Voiceover] Saved narration audio to {audio_path}")

    alignment = data.get("alignment", {})
    if not alignment:
        print("[Voiceover] WARNING: No alignment data returned by ElevenLabs.")

    return audio_path, alignment


def _reconstruct_words(alignment: dict) -> list[dict]:
    """
    Convert character-level alignment into word-level timing.

    Returns a list of:
        {"word": "Hello", "start": 0.0, "end": 0.42}
    """
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])

    words = []
    current_word = ""
    word_start = None

    for i, char in enumerate(chars):
        if char == " " or char == "\n":
            # End of a word
            if current_word:
                words.append({
                    "word": current_word,
                    "start": word_start,
                    "end": ends[i - 1] if i > 0 else 0.0,
                })
                current_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i]
            current_word += char

    # Flush last word
    if current_word:
        words.append({
            "word": current_word,
            "start": word_start,
            "end": ends[-1] if ends else 0.0,
        })

    return words


def map_segments_to_time_ranges(
    segments: list[dict],
    alignment: dict,
    audio_path: Path | None = None,
) -> list[dict]:
    """
    Using the character-level alignment data, figure out the audio start/end
    time for each segment based on its text content. Then calculate each
    segment's full "slot" — from its audio_start to the next segment's
    audio_start (or end of audio for the last segment). This ensures clips
    fill the silence gaps between narration segments.

    Mutates each segment in-place by adding:
        - "audio_start": float (seconds) — when this segment's speech begins
        - "audio_end":   float (seconds) — when this segment's speech ends
        - "slot_start":  float (seconds) — when this segment's clip should start
        - "slot_end":    float (seconds) — when this segment's clip should end
        - "slot_duration": float (seconds) — total clip duration needed

    Returns the enriched segment list.
    """
    words = _reconstruct_words(alignment)

    # Determine total audio duration
    ends = alignment.get("character_end_times_seconds", [])
    total_audio_duration = ends[-1] if ends else 60.0

    # If we have the audio file, use its actual duration (more accurate)
    if audio_path and audio_path.exists():
        try:
            from moviepy import AudioFileClip
            audio_clip = AudioFileClip(str(audio_path))
            total_audio_duration = audio_clip.duration
            audio_clip.close()
        except Exception:
            pass  # fall back to timestamp-based duration

    if not words:
        # Fallback: distribute evenly (should rarely happen)
        print("[Voiceover] WARNING: No word timing — distributing segments evenly.")
        total_chars = sum(len(s["text"]) for s in segments)

        cursor = 0.0
        for seg in segments:
            proportion = len(seg["text"]) / total_chars
            seg["audio_start"] = round(cursor, 3)
            cursor += proportion * total_audio_duration
            seg["audio_end"] = round(cursor, 3)
            seg["slot_start"] = seg["audio_start"]
            seg["slot_end"] = seg["audio_end"]
            seg["slot_duration"] = round(seg["slot_end"] - seg["slot_start"], 3)
        return segments

    # Match words from the alignment to each segment's text
    word_idx = 0
    for seg in segments:
        seg_words = seg["text"].split()
        if not seg_words:
            seg["audio_start"] = words[word_idx]["start"] if word_idx < len(words) else 0.0
            seg["audio_end"] = seg["audio_start"]
            continue

        # Find the starting word in the alignment that matches this segment
        seg_start = words[word_idx]["start"] if word_idx < len(words) else 0.0

        # Advance through alignment words by the count of words in this segment
        words_to_consume = len(seg_words)
        seg_end = seg_start
        for _ in range(words_to_consume):
            if word_idx < len(words):
                seg_end = words[word_idx]["end"]
                word_idx += 1

        seg["audio_start"] = round(seg_start, 3)
        seg["audio_end"] = round(seg_end, 3)

    # Now calculate the full slot for each segment:
    # slot runs from this segment's audio_start to the NEXT segment's audio_start
    # (or to the end of the audio for the last segment)
    for i, seg in enumerate(segments):
        seg["slot_start"] = seg["audio_start"]

        if i < len(segments) - 1:
            # Slot extends until the next segment's narration begins
            seg["slot_end"] = segments[i + 1]["audio_start"]
        else:
            # Last segment: slot extends to the end of the audio
            seg["slot_end"] = round(total_audio_duration, 3)

        seg["slot_duration"] = round(seg["slot_end"] - seg["slot_start"], 3)

        print(
            f"[Voiceover] Segment {seg['segment_id']}: "
            f"speech {seg['audio_start']:.2f}s–{seg['audio_end']:.2f}s | "
            f"slot {seg['slot_start']:.2f}s–{seg['slot_end']:.2f}s "
            f"({seg['slot_duration']:.2f}s)"
        )

    return segments
