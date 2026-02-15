# Long-Form Content Roadmap (1+ Hour Videos)

## Current State — COMPLETE

**All long-form features have been implemented and tested.** The pipeline successfully produced a 62-minute video (588 segments, ~49K char script) in a single unattended run. This document is kept for historical reference.

A 1-hour video at a calm narration pace (~130 words/minute) is roughly:
- **7,800 words** / **~40,000 characters** of script
- **80–150+ visual segments**
- **80–150+ stock footage clips** to download
- **108,000 frames** to render at 1080p 30fps

---

## Immediate Fixes (Do Before Long-Form)

These are small issues discovered during deep analysis that affect correctness
even on shorter content:

### A. Add `max_tokens` to LLM API Calls

**Problem:** Our `llm.py` does not set `max_tokens`. Kimi K2.5 may default to a
low output cap (reports suggest as low as 1,024 tokens). If the EDL JSON or
segment analysis gets silently truncated, we get invalid JSON and the pipeline
crashes. This risk exists even for short videos but gets worse with scale.

**Fix:** Set `max_tokens` explicitly (e.g., 16384) in the request body. Low effort.

### B. Make ElevenLabs Model Configurable

**Problem:** The TTS model (`eleven_multilingual_v2`) is hardcoded in
`voiceover.py`. Users can't change it without editing source code.

**Fix:** Add `ELEVENLABS_MODEL` to `.env` / `config.py` with a default of
`eleven_multilingual_v2`. This lets advanced users experiment with other models
(Turbo v2.5, Flash v2.5, v3) while keeping our quality default.

**Why we keep `eleven_multilingual_v2`:** ElevenLabs' own model selection guide
recommends it for content creation, narration, and voiceovers. It is described as
their "most lifelike" model and "most stable on long-form generations." Flash and
Turbo models trade quality for speed/cost — speed we don't need since our audio is
pre-rendered, not real-time.

---

## What Needs to Change

### 1. ElevenLabs TTS — Chunked Audio with Request Stitching (CRITICAL)

**Problem:** `eleven_multilingual_v2` has a **10,000 character** per-request limit
(~10 minutes of audio). Our current code sends the entire script in one API call.
A 40,000-character script (1 hour) will be rejected.

**Solution — Chunking + Request Stitching:**
- Split the script into chunks at **segment boundaries** (never mid-sentence),
  staying under 10,000 characters per chunk
- Use ElevenLabs' **Request Stitching** feature (`previous_request_ids` parameter)
  to maintain consistent voice prosody across chunks — this prevents abrupt tone
  shifts at chunk boundaries
- Capture the `request-id` response header from each API call and pass the
  accumulated list to subsequent calls
- Concatenate the audio bytes from all chunks in order
- Offset the character-level timestamp data so timing is continuous across chunks
  (chunk 2's timestamps start where chunk 1's audio ended)
- Keep track of cumulative duration across chunks

**Request Stitching details (from ElevenLabs docs):**
- Pass `previous_request_ids` (list of up to ~3 prior request IDs) in the request body
- The API uses these to condition the voice so transitions sound natural
- Request IDs must be used within 2 hours of generation
- NOT available for `eleven_v3` model, but works for `eleven_multilingual_v2`

**For a 1-hour video (~40,000 chars):** ~4 chunks of ~10,000 chars each.
Generation time ~2–4 minutes total (sequential, not parallelizable due to stitching).

**Memory note:** The `with_timestamps` endpoint returns `audio_base64` in the
response body. For 10-minute chunks this is manageable (~15MB per chunk). For even
longer content, consider the streaming timestamps endpoint
(`/stream/with-timestamps`) which returns data progressively.

**Estimated effort:** Medium — chunking logic is straightforward, but aligning
timestamps across chunks and integrating Request Stitching needs care.

---

### 2. Pexels API — Rate Limit Handling (CRITICAL)

**Problem:** Pexels allows **200 requests/hour** and **20,000/month**. Each segment
currently makes 1–2 API calls (initial search + possible retry with broader terms).
For 150 segments, that's up to **300 requests** — 1.5x the hourly limit. Our
current `time.sleep(0.5)` between requests is insufficient; we'd burn through the
limit in under 2 minutes.

**Solution:**
- **Rate limiter wrapper:** Track request count and timestamps in a sliding window.
  When approaching 180 requests in the current hour (leaving 20 as headroom),
  automatically pause and wait until the window resets
- **Progress logging:** Print estimated wait time so the user knows it's pausing,
  not stuck
- **Search result caching:** Cache Pexels search results (keyword → response JSON)
  in the project folder so re-runs of the same script skip redundant API calls
- **Clip reuse cache:** If a clip was already downloaded for this project, skip
  the download (already implemented via `dest.exists()` check)

**Quick win — request unlimited access:**
Pexels grants **unlimited API requests for free** to applications that provide
proper attribution. Email `api@pexels.com` with your API key + screenshots showing
Pexels/photographer credit in your output. Even with unlimited access, the rate
limiter is good defensive engineering.

**For a 1-hour video with rate limiting:** Footage retrieval stage may take
30–60 minutes due to pauses. This is expected and should be clearly communicated
to the user in console output.

**Estimated effort:** Low — add a rate limiter class wrapping the Pexels API calls.

---

### 3. LLM Timeline Builder — Batched EDL Generation (MODERATE)

**Problem:** Sending 100+ segments with full metadata to the LLM in one prompt
risks two failures:
1. **Output truncation** — even with `max_tokens` set, a 100+ entry JSON array
   may exceed the response limit (Kimi K2.5's max output is ~32K tokens)
2. **Timeout** — large prompts + large responses increase processing time beyond
   our 300-second timeout

**Kimi K2.5 rate limits by tier:**

| Tier | Cumulative Recharge | RPM | TPM |
|------|---------------------|-----|-----|
| Tier 0 | $1 | 3 | 500K |
| Tier 1 | $10 | 200 | 2M |
| Tier 2 | $20 | 500 | 3M |

At Tier 0 (3 RPM), batched EDL generation with 5–6 calls works but needs ~2 min
of delay. Tier 1+ is recommended for production use.

**Solution:**
- Process segments in **batches of 20–30** at a time
- Each batch generates a partial EDL (JSON array)
- Concatenate all partial EDLs into the full timeline
- Pass minimal context between batches: the last segment's transition type and
  mood, so the first entry of the next batch can maintain visual continuity
- Add a small delay between batches to respect RPM limits

**Estimated effort:** Medium — need to handle batch boundaries and transition
continuity.

---

### 4. Checkpoint / Resume — Skip Completed Stages (MODERATE)

**Problem:** If the pipeline fails at Stage 5 (rendering) for a 1-hour video,
re-running currently repeats all stages from scratch. This means re-downloading
100+ clips and re-generating 40 minutes of TTS audio — wasting time and API
credits.

**Solution:**
- Before each stage, check if its output files already exist in the project folder
  (e.g., `1_segments.json`, `2_segments_with_footage.json`, `3_alignment.json`,
  `narration.mp3`, `4_edl.json`)
- If the intermediate files exist and are valid, **skip that stage** and load from
  the saved files
- Add a `--fresh` flag to force re-running all stages from scratch
- Print clearly which stages are being skipped vs. re-run

**This also helps during development** — you can iterate on the video assembler
without waiting for footage downloads and TTS generation every time.

**Estimated effort:** Medium — need to add detection + loading logic for each stage,
and validate that saved files are compatible with the current run.

---

### 5. Video Rendering — Performance Optimization (MODERATE)

**Problem:** At ~3 frames/second, rendering 1 hour of 1080p video takes roughly
10 hours.

**Options:**
- **Faster FFmpeg preset:** Change from `"medium"` to `"fast"` or `"ultrafast"` —
  trades file size for speed (2–5x faster)
- **Lower intermediate resolution:** Render at 720p for drafts, 1080p for final
- **GPU acceleration:** Use FFmpeg hardware encoding (NVENC for Nvidia, QSV for
  Intel) if available — dramatically faster
- **Progressive rendering:** Render in chunks and concatenate at the end — allows
  resuming if interrupted
- **Auto-detect CPU threads:** Replace hardcoded `threads=4` with
  `os.cpu_count()` for better defaults across machines

**Recommended first step:** Switch to `"fast"` preset for drafts. Add a
`--quality` flag (draft/final).

**Estimated effort:** Low for preset change, Medium for GPU acceleration.

---

### 6. Memory Management — Clip Loading (LOW PRIORITY)

**Problem:** Loading 100+ HD video clips into MoviePy simultaneously could use
several GB of RAM.

**Solution:**
- Process and render clips in **batches** (e.g., 20 at a time)
- Render each batch to a temporary file
- Concatenate the batch files at the end using FFmpeg's concat demuxer (fast, no
  re-encoding)
- This also helps with the rendering performance issue (smaller render jobs are
  more stable)

**Estimated effort:** Medium — requires restructuring the video assembler.

---

## Implementation Priority — All Complete

| Priority | Task | Status |
|---|---|---|
| 0a | Add `max_tokens` to LLM calls | **DONE** — set to 32,768 |
| 0b | Make ElevenLabs model configurable | Deferred (hardcoded default works well) |
| 1 | ElevenLabs chunked TTS + Request Stitching | **DONE** — 9,500 char chunks with stitching |
| 2 | Pexels rate limit handling | **DONE** — sliding-window RateLimiter class |
| 3 | LLM batched EDL generation | **DONE** — 25 segments per batch |
| 4 | Checkpoint / resume | **DONE** — all stages cached as JSON |
| 5 | Render preset optimization | **DONE** — `--quality draft/final` flag |
| 6 | Memory-efficient rendering | **DONE** — FFmpeg-direct replaced MoviePy entirely |
| 6b | Chunked script analysis | **DONE** — 5K char chunks with retry logic (discovered during 1-hour test) |
| 6c | Audio mastering | **DONE** — 3-stage chain: force mono, dynaudnorm (per-frame levelling), loudnorm (EBU R128 -16 LUFS), stereo output |

---

## What Stays the Same

These parts of the pipeline scale fine without changes:
- **Slot-based timing logic** — works the same regardless of video length
- **Per-script folder organization** — already isolated per project
- **Auto-versioning** — already works
- **Audio muting** — already works
- **Overall architecture** — the pipeline design is sound, just needs chunking at
  the edges

**Note on script analysis:** Kimi K2.5's 262K token context handles long scripts
well for *input*, but the *output* (a large JSON array of segments) has the same
`max_tokens` truncation risk as the EDL builder. Fix 0a addresses this. For very
long scripts (2+ hours), batching the script analysis may also be needed.

---

## ElevenLabs Model Reference

For context on why we default to `eleven_multilingual_v2` and what alternatives
exist:

| Model | Quality | Char Limit | Cost (credits/char) | Best For |
|---|---|---|---|---|
| `eleven_v3` | Highest — dramatic, expressive | 5,000 | 1 | Emotional dialogue, characters |
| `eleven_multilingual_v2` | **High — lifelike, stable** | **10,000** | **1** | **Content creation, narration** |
| `eleven_turbo_v2_5` | Good — balanced | 40,000 | 0.5 | Mid-ground quality/speed |
| `eleven_flash_v2_5` | Acceptable — fast | 40,000 | 0.5 | Conversational AI, chatbots |

- `eleven_multilingual_v2` is ElevenLabs' recommended model for voiceovers,
  audiobooks, and video narration. It has the richest emotional expression and is
  the most stable on long-form generations.
- Turbo/Flash models have 4x the character limit and cost half as much, but trade
  quality and emotional depth for speed we don't need (our audio is pre-rendered).
- `eleven_v3` is the most expressive but has the lowest character limit (5,000)
  and does NOT support Request Stitching.
- Request Stitching (required for chunking) works with `multilingual_v2`,
  `turbo_v2_5`, and `flash_v2_5` — but NOT `eleven_v3`.

---

## Stretch Goals (After Long-Form Works)

### Full Video Production Workflow (High Priority)

These features close the gap between "pipeline produces a video" and "video is
ready to publish." Currently these post-production steps are done manually.

- **Script generation from topic** — Given a topic, use deep research + LLM to
  generate a complete 20+ minute video script automatically. Makes the workflow
  truly end-to-end: topic in, publish-ready video out.
- **Video title generation** — Auto-generate a CTR-optimized YouTube title from
  the script content. Save to workspace folder alongside the video.
- **Video description generation** — Auto-generate a YouTube description with
  summary, timestamps (from segment timing data we already have), and Pexels
  attribution (from credits.txt we already generate).
- **Thumbnail prompt generation** — Auto-generate a Midjourney/DALL-E prompt for
  a custom thumbnail based on the script's topic, tone, and key visuals.

### Video Quality & Features

- ~~**Text overlay system** — Pillow-generated styled PNG overlays for quotes,
  statistics, and source citations. Composited onto video via FFmpeg with
  fade-in / fade-out animation.~~ **DONE** — implemented in `text_overlay.py`
  with three card types: direct-quote, statistic, source-citation. Opt-in via
  `--overlays` flag (experimental — alignment can vary).
- ~~**Subtitle / caption generation** — Burn captions into the video using the
  timestamp data we already have.~~ **DONE** — implemented in `captions.py` +
  `video_assembler.py`. SRT generated from ElevenLabs word-level timing, burned in
  via FFmpeg `subtitles` filter with ASS styling. Opt-in via `--captions` flag,
  opt-in via `--captions`. Landscape: bottom-center, 8 words/cue. Vertical:
  lower-third safe zone, 5 words/cue.
- ~~**Vertical short-form support** — 9:16 output for TikTok, Reels, YouTube
  Shorts.~~ **DONE** — `--vertical` flag switches to 1080x1920, auto-pulls portrait
  footage from Pexels, auto-enables captions with shorter cues, positions captions
  in lower-third safe zone above platform UI buttons.
- **Background music layer** — Add a subtle ambient track under the narration
- **Automatic transitions** — Crossfades, dissolves between clips

### Workflow & Tooling

- **Batch processing** — Run multiple scripts in sequence overnight
- **Web UI** — Simple interface for uploading scripts and downloading videos
- **Cost estimation** — Log estimated API costs (ElevenLabs credits, LLM tokens)
  before running the pipeline, so the user can confirm
- **GPU-accelerated encoding** — Use NVENC/QSV for faster rendering on supported
  hardware

### Voice & Audio

- **Voice stability improvements** — ElevenLabs voice settings (stability=0.75,
  use_speaker_boost=True) were tuned to reduce whispering artifacts in the narrator.
  Further tuning or model switching may improve this further for different voice IDs.
