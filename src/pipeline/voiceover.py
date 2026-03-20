"""
Stage 3 — Voiceover Generation + Timestamp Extraction

Supports two TTS providers (set TTS_PROVIDER in .env):

  * "elevenlabs" — ElevenLabs with character-level timestamps and Request
    Stitching for multi-chunk prosody continuity.
  * "fish_audio" — Fish Audio (pay-as-you-go, ~$0.10 per 10-min video).
    Does not return timestamps, so alignment is synthesised from the
    audio duration using proportional character mapping.

After generation, audio is post-processed with a 3-stage FFmpeg filter chain:
  1. aformat  — Force mono (safety net for consistent channel layout)
  2. dynaudnorm — Dynamic per-frame volume levelling
  3. loudnorm — EBU R128 loudness normalization (-16 LUFS, YouTube target)
Output is duplicated to stereo (-ac 2) for universal playback compatibility.
"""

import base64
import subprocess
from pathlib import Path
import requests

from src.config import (
    TTS_PROVIDER,
    ELEVENLABS_API_KEY,
    ELEVENLABS_BASE_URL,
    ELEVENLABS_VOICE_ID,
    FISH_AUDIO_API_KEY,
    FISH_AUDIO_VOICE_ID,
    FISH_AUDIO_MODEL,
)

_EL_CHAR_LIMIT = 9500  # ElevenLabs eleven_multilingual_v2 limit (with buffer)
_FA_CHAR_LIMIT = 8000  # Fish Audio practical limit per request


# ===================================================================
#  Audio post-processing
# ===================================================================

def _normalize_audio(audio_path: Path) -> None:
    """Loudness-normalise and convert to stereo for YouTube."""
    temp_path = audio_path.with_suffix(".norm.mp3")
    af_chain = (
        "aformat=channel_layouts=mono,"
        "dynaudnorm=framelen=500:gausssize=31:peak=0.95:maxgain=10,"
        "loudnorm=I=-16:TP=-1.5:LRA=11:dual_mono=true"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-af", af_chain,
        "-ac", "2",
        "-q:a", "2",
        str(temp_path),
    ]
    print("[Voiceover] Normalizing audio (dynaudnorm + loudnorm + stereo)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[Voiceover] WARNING: Audio normalization failed: {result.stderr[:300]}")
        temp_path.unlink(missing_ok=True)
        return

    import time as _time
    for attempt in range(5):
        try:
            audio_path.unlink(missing_ok=True)
            temp_path.rename(audio_path)
            print("[Voiceover] Audio normalized successfully.")
            return
        except PermissionError:
            if attempt < 4:
                _time.sleep(1 * (attempt + 1))
            else:
                print("[Voiceover] WARNING: Could not replace original audio (file locked). Using normalized copy.")
                audio_path = temp_path


def _get_audio_duration(audio_path: Path) -> float:
    """Get duration in seconds via ffprobe (no heavy deps)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        pass
    try:
        from moviepy import AudioFileClip
        clip = AudioFileClip(str(audio_path))
        dur = clip.duration
        clip.close()
        return dur
    except Exception:
        return 0.0


def _build_proportional_alignment(text: str, total_duration: float) -> dict:
    """
    Synthesise character-level alignment from total audio duration.
    Distributes time proportionally by character count — accurate enough
    for segment-to-video mapping when the TTS provider doesn't return
    native timestamps.
    """
    if total_duration <= 0 or not text:
        return {}

    chars = list(text)
    n = len(chars)
    time_per_char = total_duration / n

    starts = []
    ends = []
    cursor = 0.0
    for _ in chars:
        starts.append(round(cursor, 4))
        cursor += time_per_char
        ends.append(round(cursor, 4))

    return {
        "characters": chars,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }


# ===================================================================
#  Public entry point
# ===================================================================

def generate_voiceover(script_text: str, audio_dir: Path) -> tuple[Path, dict]:
    """
    Generate narration audio with alignment timestamps.

    Dispatches to ElevenLabs or Fish Audio based on TTS_PROVIDER.

    Returns:
        (audio_path, alignment_data)
    """
    provider = TTS_PROVIDER

    if provider == "fish_audio":
        if not FISH_AUDIO_API_KEY:
            raise RuntimeError("FISH_AUDIO_API_KEY is not set. Add it to your .env file.")
        print(f"[Voiceover] Using Fish Audio (model: {FISH_AUDIO_MODEL})")
        return _fish_audio_generate(script_text, audio_dir)

    # Default: ElevenLabs
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set. Add it to your .env file.")
    print("[Voiceover] Using ElevenLabs")
    if len(script_text) <= _EL_CHAR_LIMIT:
        return _elevenlabs_single(script_text, audio_dir)
    else:
        return _elevenlabs_chunked(script_text, audio_dir)


# ===================================================================
#  Fish Audio provider
# ===================================================================

_FA_BASE_URL = "https://api.fish.audio"


def _fish_audio_tts_request(text: str) -> bytes:
    """Single Fish Audio TTS call. Returns raw MP3 bytes."""
    url = f"{_FA_BASE_URL}/v1/tts"
    body: dict = {
        "text": text,
        "format": "mp3",
        "mp3_bitrate": 192,
        "latency": "normal",
        "chunk_length": 300,
        "normalize": True,
    }
    if FISH_AUDIO_VOICE_ID:
        body["reference_id"] = FISH_AUDIO_VOICE_ID

    from src.utils.retry import retry as _retry

    @_retry(max_attempts=3, base_delay=5.0, max_delay=30.0,
            exceptions=(requests.RequestException,))
    def _do_post():
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {FISH_AUDIO_API_KEY}",
                "Content-Type": "application/json",
                "model": FISH_AUDIO_MODEL,
            },
            json=body,
            timeout=600,
            stream=True,
        )
        if not r.ok:
            try:
                err_detail = r.json()
            except Exception:
                err_detail = r.text[:500]
            print(f"[Voiceover] Fish Audio API error {r.status_code}: {err_detail}")
        r.raise_for_status()
        return r.content

    return _do_post()


def _fish_audio_generate(script_text: str, audio_dir: Path) -> tuple[Path, dict]:
    """Generate audio via Fish Audio, with chunking for long scripts."""
    audio_path = audio_dir / "narration.mp3"

    if len(script_text) <= _FA_CHAR_LIMIT:
        print(f"[Voiceover] Script is {len(script_text):,} chars — single request")
        audio_bytes = _fish_audio_tts_request(script_text)
        audio_path.write_bytes(audio_bytes)
    else:
        chunks = _split_into_chunks(script_text, _FA_CHAR_LIMIT)
        print(
            f"[Voiceover] Script is {len(script_text):,} chars — "
            f"splitting into {len(chunks)} chunks"
        )
        all_audio: list[bytes] = []
        for i, chunk_text in enumerate(chunks):
            print(f"[Voiceover] Generating chunk {i + 1}/{len(chunks)} ({len(chunk_text):,} chars)...")
            audio_bytes = _fish_audio_tts_request(chunk_text)
            all_audio.append(audio_bytes)
            print(f"[Voiceover] Chunk {i + 1}: received {len(audio_bytes):,} bytes")
        audio_path.write_bytes(b"".join(all_audio))

    print(f"[Voiceover] Saved narration audio to {audio_path}")
    _normalize_audio(audio_path)

    total_duration = _get_audio_duration(audio_path)
    print(f"[Voiceover] Audio duration: {total_duration:.1f}s")

    alignment = _build_proportional_alignment(script_text, total_duration)
    if not alignment:
        print("[Voiceover] WARNING: Could not build alignment data.")

    return audio_path, alignment


# ===================================================================
#  ElevenLabs provider
# ===================================================================

def _elevenlabs_tts_request(
    text: str,
    previous_request_ids: list[str] | None = None,
) -> tuple[bytes, dict, str]:
    """Single ElevenLabs TTS API call with timestamps."""
    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{ELEVENLABS_VOICE_ID}/with-timestamps"

    body: dict = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.75,
            "similarity_boost": 0.75,
            "use_speaker_boost": True,
        },
    }
    if previous_request_ids:
        body["previous_request_ids"] = previous_request_ids

    from src.utils.retry import retry as _retry

    @_retry(max_attempts=3, base_delay=5.0, max_delay=30.0,
            exceptions=(requests.RequestException,))
    def _do_tts_post():
        r = requests.post(
            url,
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
        )
        if not r.ok:
            try:
                err_detail = r.json()
            except Exception:
                err_detail = r.text[:500]
            print(f"[Voiceover] ElevenLabs API error {r.status_code}: {err_detail}")
        r.raise_for_status()
        return r

    resp = _do_tts_post()

    try:
        from src.utils.quota_tracker import record_elevenlabs_chars
        record_elevenlabs_chars(len(text))
    except Exception:
        pass

    data = resp.json()
    audio_bytes = base64.b64decode(data["audio_base64"])
    alignment = data.get("alignment", {})
    request_id = resp.headers.get("request-id", "")

    return audio_bytes, alignment, request_id


def _elevenlabs_single(script_text: str, audio_dir: Path) -> tuple[Path, dict]:
    """Generate audio in a single ElevenLabs API call."""
    print(f"[Voiceover] Script is {len(script_text):,} chars — single request")
    audio_bytes, alignment, _ = _elevenlabs_tts_request(script_text)

    audio_path = audio_dir / "narration.mp3"
    audio_path.write_bytes(audio_bytes)
    print(f"[Voiceover] Saved narration audio to {audio_path}")

    _normalize_audio(audio_path)

    if not alignment:
        print("[Voiceover] WARNING: No alignment data returned by ElevenLabs.")

    return audio_path, alignment


def _elevenlabs_chunked(script_text: str, audio_dir: Path) -> tuple[Path, dict]:
    """Generate audio for long scripts via ElevenLabs Request Stitching."""
    chunks = _split_into_chunks(script_text, _EL_CHAR_LIMIT)
    print(
        f"[Voiceover] Script is {len(script_text):,} chars — "
        f"splitting into {len(chunks)} chunks (limit: {_EL_CHAR_LIMIT:,} chars/chunk)"
    )

    request_ids: list[str] = []
    chunk_audio_parts: list[bytes] = []
    merged_alignment: dict = {
        "characters": [],
        "character_start_times_seconds": [],
        "character_end_times_seconds": [],
    }
    cumulative_duration = 0.0

    for i, chunk_text in enumerate(chunks):
        print(
            f"[Voiceover] Generating chunk {i + 1}/{len(chunks)} "
            f"({len(chunk_text):,} chars)..."
        )

        prev_ids = request_ids[-3:] if request_ids else None
        audio_bytes, alignment, request_id = _elevenlabs_tts_request(chunk_text, prev_ids)

        if request_id:
            request_ids.append(request_id)

        chunk_duration = 0.0
        temp_path = audio_dir / f"_chunk_{i}.mp3"
        temp_path.write_bytes(audio_bytes)
        try:
            from moviepy import AudioFileClip
            clip = AudioFileClip(str(temp_path))
            chunk_duration = clip.duration
            clip.close()
        except Exception:
            chunk_ends = alignment.get("character_end_times_seconds", [])
            chunk_duration = chunk_ends[-1] if chunk_ends else 0.0
        finally:
            temp_path.unlink(missing_ok=True)

        for start in alignment.get("character_start_times_seconds", []):
            merged_alignment["character_start_times_seconds"].append(
                round(start + cumulative_duration, 4)
            )
        for end in alignment.get("character_end_times_seconds", []):
            merged_alignment["character_end_times_seconds"].append(
                round(end + cumulative_duration, 4)
            )
        merged_alignment["characters"].extend(
            alignment.get("characters", [])
        )

        chunk_audio_parts.append(audio_bytes)
        cumulative_duration += chunk_duration

        print(
            f"[Voiceover] Chunk {i + 1}: {chunk_duration:.2f}s "
            f"(cumulative: {cumulative_duration:.2f}s)"
        )

    audio_path = audio_dir / "narration.mp3"
    audio_path.write_bytes(b"".join(chunk_audio_parts))

    print(
        f"[Voiceover] Saved combined narration "
        f"({cumulative_duration:.1f}s) to {audio_path}"
    )

    _normalize_audio(audio_path)

    if not merged_alignment["characters"]:
        print("[Voiceover] WARNING: No alignment data returned by ElevenLabs.")

    return audio_path, merged_alignment


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """
    Split text into chunks of at most max_chars, breaking at sentence
    boundaries (. ? !) to avoid cutting mid-sentence.
    Falls back to word boundaries if no sentence end is found.
    """
    chunks = []
    remaining = text.strip()

    while len(remaining) > max_chars:
        # Find the last sentence-ending punctuation before the limit
        split_at = -1
        for sep in [". ", "? ", "! ", ".\n", "?\n", "!\n"]:
            idx = remaining[:max_chars].rfind(sep)
            if idx > split_at:
                split_at = idx + len(sep)

        # If no sentence boundary found, split at last space
        if split_at <= 0:
            split_at = remaining[:max_chars].rfind(" ")
        if split_at <= 0:
            split_at = max_chars  # hard split as last resort

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


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
        if char in (" ", "\n", "\r"):
            # End of a word (include \r for scripts authored on Windows)
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
