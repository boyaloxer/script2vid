# Hurdles & Fixes

A log of issues encountered during development and how they were resolved.
Useful for debugging if similar problems resurface.

---

## 1. MoviePy Import Hangs Indefinitely on Windows

**Symptom:** `import moviepy` freezes forever — no error, no output. Python process stays alive but never progresses past the import.

**Root Cause:** MoviePy 2.x (via `imageio_ffmpeg`) runs FFmpeg subprocess calls during import to auto-detect the binary. On Windows, this detection mechanism can deadlock — the subprocess pipes fill up or the process never exits, causing Python to wait forever.

**How we found it:** Verbose import tracing (`python -v`) showed the hang occurred after `imageio.plugins.ffmpeg` imported `socket`, right where MoviePy probes FFmpeg. Individual `imageio_ffmpeg` submodules imported fine, but the full package (which triggers the probe) hung.

**Fix:** Set the `IMAGEIO_FFMPEG_EXE` environment variable *before* MoviePy imports, bypassing the broken auto-detection entirely. Added to `src/config.py` (which is always imported first):

```python
import shutil
if not os.environ.get("IMAGEIO_FFMPEG_EXE"):
    _ffmpeg = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
    if _ffmpeg:
        os.environ["IMAGEIO_FFMPEG_EXE"] = _ffmpeg
```

Users can also override via `FFMPEG_PATH` in `.env` if needed.

---

## 2. MemoryError During Video Rendering (120 Clips)

**Symptom:** `MemoryError` at clip ~89 of 120 during Stage 5 rendering. Python crashes with an out-of-memory error inside MoviePy's frame reader.

**Root Cause:** The original assembler loaded *all* clips into memory simultaneously, then concatenated them. For 120 HD clips (a ~12-minute video), this exceeded available RAM.

**Fix:** Refactored `src/video_assembler.py` to use **chunked rendering**:
- Clips are processed in batches of ~30, rendered to temporary `.mp4` files, then memory is freed.
- Temporary files are joined using **FFmpeg's concat demuxer** (`-f concat -c copy`), which is nearly instant and requires no re-encoding.
- Narration audio is overlaid in a separate FFmpeg pass (also no video re-encode).
- Small EDLs (<=30 clips) still use the fast single-pass path.

---

## 3. MoviePy 2.x API Changes (MultiplyVolume Removed)

**Symptom:** `AttributeError: module 'moviepy.video.fx' has no attribute 'MultiplyVolume'`

**Root Cause:** MoviePy 2.x removed `vfx.MultiplyVolume`. The old 1.x API for adjusting audio volume no longer exists.

**Fix:** Replaced with `clip.without_audio()` since our design strips all clip audio anyway (narrator-only). For cases where volume adjustment is needed, MoviePy 2.x uses scalar multiplication: `clip.audio * 0.1`.

---

## 4. LLM API 401 Unauthorized (Kimi K2.5)

**Symptom:** `requests.exceptions.HTTPError: 401 Client Error: Unauthorized` when calling the Kimi API.

**Root Cause:** Two incorrect defaults:
- Base URL was `https://api.moonshot.cn/v1` (Chinese endpoint) instead of `https://api.moonshot.ai/v1` (international).
- Model name was `kimi-2.5` instead of `kimi-k2.5`.

**Fix:** Corrected both in `.env.example` and `src/config.py`. Also made `temperature` optional in `src/llm.py` since Kimi K2.5 is strict about parameter validation.

---

## 5. LLM Read Timeout on Large Prompts

**Symptom:** `requests.exceptions.ReadTimeout` after 120 seconds during timeline building.

**Root Cause:** The default 120-second timeout was too short for the LLM to process large segment lists and generate detailed EDL JSON.

**Fix:** Increased timeout in `src/llm.py` from 120 to 300 seconds. Also implemented batched EDL generation (25 segments per batch) to keep individual LLM calls manageable.

---

## 6. Audio-Video Sync Drift

**Symptom:** Video clips were relevant to the narration at the start, but gradually fell behind as the video progressed. By the end, the visuals were significantly ahead of the audio.

**Root Cause:** Each clip was trimmed to match only the *speech* duration of its segment. But narration has natural pauses between sentences — those gaps weren't accounted for, so clips ran shorter than the actual audio timeline.

**Fix:** Implemented **slot-based timing**. Each clip now fills the entire time slot from the start of its narration to the start of the *next* segment's narration (speech + subsequent pause). This keeps visuals continuously synchronized with the audio.

---

## 7. KeyError: 'audio_start' After Timing Refactor

**Symptom:** `KeyError: 'audio_start'` during EDL sorting in the video assembler.

**Root Cause:** After switching to slot-based timing, EDL entries used `slot_start` instead of `audio_start`, but the sort key still referenced the old field.

**Fix:** Updated the sort to `e.get("slot_start", e.get("audio_start", 0))` for backward compatibility.

---

## 8. NameError: 'OUTPUT_DIR' After Config Refactor

**Symptom:** `NameError: name 'OUTPUT_DIR' is not defined` in `video_assembler.py`.

**Root Cause:** When `config.py` was refactored from global path variables to the per-script `create_project_dirs()` function, `video_assembler.py` still referenced the old global.

**Fix:** Updated `assemble_video` to accept `output_dir` as a parameter, passed in from `main.py`.

---

## 9. pip Install Permission Denied

**Symptom:** `OSError: [Errno 13] Permission denied` when running `pip install -r requirements.txt`.

**Root Cause:** The command was run inside a sandboxed environment that restricted filesystem writes outside the workspace directory.

**Fix:** Ran the install with full system permissions (outside sandbox).

---

## 10. PowerShell `&&` Not Supported

**Symptom:** `The token '&&' is not a valid statement separator in this version.`

**Root Cause:** PowerShell (the default shell on this Windows system) doesn't support `&&` as a command chain operator in older versions.

**Fix:** Use semicolons (`;`) for PowerShell, or use the shell tool's `working_directory` parameter instead of `cd && command`.

---

## 11. UnicodeEncodeError on Windows Console

**Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` during Stage 5 rendering progress output.

**Root Cause:** A print statement in `video_assembler.py` used the Unicode arrow character `→` (`\u2192`). The Windows console's default encoding (`cp1252`) doesn't support this character.

**Fix:** Replaced `→` with the ASCII equivalent `->` in the progress message f-string.

---

## 12. LLM Timeout on Large Script Analysis (49K chars)

**Symptom:** `requests.exceptions.ReadTimeout` during Stage 1 when processing a 49,000-character script for a 1-hour video. The entire script was sent as a single LLM call.

**Root Cause:** The script analyzer had no chunking — it sent the full script text to the LLM in one request. For a 49K-char script, the LLM needed to generate a massive JSON response (hundreds of segments), which exceeded the 300-second timeout.

**Fix:** Implemented chunked script analysis in `script_analyzer.py`:
- Scripts over 5,000 chars are split at paragraph boundaries into ~5K-char chunks
- Each chunk is processed in a separate LLM call
- Segments from all chunks are merged and renumbered sequentially
- Added retry logic (up to 3 attempts with backoff) for each chunk

---

## 13. LLM JSON Truncation on Script Analysis Chunks

**Symptom:** `json.decoder.JSONDecodeError: Unterminated string starting at: line 271 column 27` — the LLM returned truncated JSON that couldn't be parsed.

**Root Cause:** Even after chunking, the initial 10K-char chunk size produced JSON responses large enough to hit the LLM's output token limit. The response was cut off mid-string.

**Fix:** Three changes:
1. Reduced chunk size from 10K to 5K chars — each chunk now produces ~15-20 segments (much less JSON output)
2. Increased `max_tokens` from 16,384 to 32,768 for more output headroom
3. Increased LLM timeout from 300s to 600s for safety

---

## 14. ElevenLabs Quota Exceeded (401 on Chunk 2)

**Symptom:** `401 Client Error: Unauthorized` on the second TTS chunk. First chunk succeeded. Error detail: `quota_exceeded — This request exceeds your quota of 40000. You have 4712 credits remaining, while 9469 credits are required.`

**Root Cause:** The ElevenLabs free/starter plan has a 40,000 character monthly quota. A 1-hour script (~49K chars) exceeds this limit. The first chunk consumed most of the quota, and the second chunk was rejected.

**Fix:** Two changes:
1. Added detailed error reporting in `voiceover.py` to show the actual ElevenLabs error body (previously it just showed "401 Unauthorized" with no detail)
2. User upgraded their ElevenLabs plan to get sufficient credits

**Lesson:** For long-form content (1+ hour), ensure your ElevenLabs plan has at least 50K+ character credits available before running the pipeline.

---

## 15. Mono Audio Routing to One Channel on Playback

**Symptom:** After normalizing the 1-hour video's audio to mono, the audio played through only the right ear/channel on headphones and some devices, instead of equally through both.

**Root Cause:** A mono AAC audio track inside an MP4 container is a single channel. Different players and devices interpret mono audio inconsistently — some route it to center (both ears), others send it to one channel (typically right). This is a known quirk with mono audio in MP4 containers.

Additionally, the original `pan=mono|c0=0.5*c0+0.5*c1` filter was being applied to already-mono input from ElevenLabs. Since `c1` doesn't exist on a mono signal, FFmpeg treated it as silence — the formula became `0.5 * audio + 0.5 * 0`, effectively **halving the volume**. `loudnorm` then had to boost the gain back up, introducing unnecessary processing.

**Fix:** Two changes:
1. Replaced `pan=mono` with `aformat=channel_layouts=mono` — uses FFmpeg's built-in mono conversion (safe no-op when input is already mono, no manual gain math)
2. Added `-ac 2` to the output — duplicates the processed mono channel to both L and R, producing a stereo file where both channels are identical. Every player handles stereo correctly.

---

## 16. Volume Spikes Making Loudness Normalization Ineffective

**Symptom:** Even after mono conversion and `loudnorm`, the audio had noticeable volume differences between TTS chunks. Loud spikes/peaks in certain sections anchored the normalization gain low, leaving quieter sections still too quiet.

**Root Cause:** `loudnorm` is a *global* normalizer — it picks one overall gain for the entire file based on integrated loudness. If there are transient spikes, it must keep the gain conservative to prevent those peaks from clipping. This means chunk-to-chunk volume differences survive normalization.

**Fix:** Added `dynaudnorm` (FFmpeg's dynamic audio normalizer) **before** `loudnorm` in the filter chain. `dynaudnorm` works frame-by-frame (500ms windows), dynamically boosting quiet sections and taming loud ones. By the time `loudnorm` runs, the signal is already levelled, so it can normalize accurately without being thrown off by peaks.

The final 3-stage audio chain is:
```
aformat=channel_layouts=mono → dynaudnorm → loudnorm (EBU R128, -16 LUFS) → stereo output (-ac 2)
```

**Lesson:** For concatenated TTS audio, global normalization alone isn't enough. Dynamic per-frame levelling (`dynaudnorm`) must precede global normalization (`loudnorm`) to produce consistent results.

---

## 17. Config Overrides Not Picked Up by Modules (--vertical flag)

**Symptom:** Running with `--vertical` printed the correct config (1080x1920) in `main.py`, but the output video was still 1920x1080 and Pexels returned landscape footage.

**Root Cause:** Modules (`video_assembler.py`, `footage_finder.py`, `text_overlay.py`) imported config values directly at module load time:
```python
from src.config import OUTPUT_WIDTH, OUTPUT_HEIGHT
```
This created local copies of the integers at import time. When `main.py` later overrode `src.config.OUTPUT_WIDTH = 1080`, the modules' local copies were already frozen at 1920.

**Fix:** Changed all affected modules to import the module reference instead:
```python
import src.config as _cfg
```
Then access values at call time via `_cfg.OUTPUT_WIDTH`, `_cfg.OUTPUT_HEIGHT`, `_cfg.OUTPUT_FPS`. This ensures runtime overrides made in `main.py` are visible everywhere.

**Lesson:** In Python, `from module import X` creates a local binding to the *value* at import time. If you need runtime-mutable config, always use `import module` and access `module.X` so you read the live attribute.

---

## 18. Vertical Captions Off-Screen / Wrong Position (ASS Coordinate System)

**Symptom:** After fixing the config import issue (#17), vertical video rendered correctly at 1080x1920, but captions were either invisible, at the top-left corner, or running off-screen.

**Root Cause:** FFmpeg's internal SRT-to-ASS conversion uses a **virtual coordinate system** with `PlayResX=384` and `PlayResY=288` by default — NOT the video's actual pixel dimensions. Our `force_style` parameters used pixel-based values (e.g., `MarginV=480` for 1920px height), which exceeded the 288-unit virtual frame and pushed text completely off-screen.

Additionally, ASS `Alignment` values follow a numpad-based system (1=bottom-left, 2=bottom-center, 5=top-left, 10=middle-center), which differs from what the naming might suggest.

**Fix:** Recalculated all ASS style parameters relative to the 384x288 virtual coordinate system:
- **Vertical mode**: `Alignment=2` (bottom-center), `MarginV=72` (~75% down the frame, translating to the lower-third safe zone on a 1920px display), `MarginL=28`, `MarginR=28`, `Fontsize=18`
- **Landscape mode**: `Alignment=2`, `MarginV=10`, `MarginL=10`, `MarginR=10`, `Fontsize=16`

Also deleted and regenerated the `captions.srt` file to ensure it had the correct `words_per_cue` setting for the current mode.

**Lesson:** When using FFmpeg's `subtitles` filter with `force_style`, all positional values (margins, font sizes) operate in ASS's virtual coordinate space (default 384x288), not in the video's pixel dimensions. Divide your desired pixel position by the video dimension, then multiply by the PlayRes value to get the correct ASS unit.

---

## 19. ElevenLabs Narrator Whispering

**Symptom:** The narrator intermittently dropped to a very soft whisper mid-sentence, making the audio jarring to listen to.

**Root Cause:** The default ElevenLabs voice settings (`stability=0.5`, no speaker boost) allowed too much variation in the generated speech. The model would occasionally interpret certain sentence structures or pauses as cues to speak more softly.

**Fix:** Updated `src/voiceover.py` voice settings:
- Increased `stability` from `0.5` to `0.75` — reduces prosody variation
- Added `use_speaker_boost: True` — maintains consistent volume level
- Kept `similarity_boost` at `0.75` for natural voice quality

**Lesson:** For narration-style TTS, higher stability values (0.7-0.8) prevent unwanted dramatic shifts. Speaker boost helps maintain consistent loudness across the entire generation.
