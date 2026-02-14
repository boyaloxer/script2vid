# script2vid — Project Outline

## Overview

script2vid is an automated video production pipeline that takes a written script as input and produces a fully assembled video with narrated voiceover and relevant stock footage — no manual editing required.

---

## Current Status: Production-Ready

The pipeline is **fully built, tested, and production-ready** for both short-form and long-form content. It has been successfully tested on videos ranging from 2 minutes to over 1 hour (62 minutes).

### What's Been Built

- **Script Analysis** — AI decomposes script into visual segments with keywords, mood, descriptions, and **quote/citation classification** (`direct_quote`, `statistic`, `source_citation`, or `none`). Chunked processing for large scripts (5K chars/chunk with retry logic).
- **Text Overlays** *(opt-in, `--overlays`)* — Pillow-generated styled PNG overlays for quotes, statistics, and source citations. Three card types: direct-quote cards (dark rounded rect with accent line), statistic callouts (large bold number with backing), and source-citation pills (small lower-right badge). Composited onto video via FFmpeg with fade-in / fade-out animation. Experimental — alignment can vary.
- **Closed Captions** *(opt-in, `--captions`; auto-enabled in vertical mode)* — SRT subtitle generation from ElevenLabs word-level timing data, burned into the video via FFmpeg's `subtitles` filter with ASS styling. Landscape mode: bottom-center, 8 words/cue. Vertical mode: lower-third safe zone (above platform UI, below visual center), 5 words/cue.
- **Vertical Short-Form Support** *(`--vertical`)* — Renders in 9:16 (1080x1920) for TikTok, Reels, and YouTube Shorts. Automatically switches Pexels searches to portrait orientation, enables captions with shorter cues, and positions captions in the lower-third safe zone.
- **Footage Retrieval** — Searches Pexels, scores/ranks results, downloads best matches, avoids repeats. Integrated rate limiter (200 req/hr sliding window). Captures Pexels attribution for credits. **Auto-detects orientation** (landscape or portrait) based on output resolution.
- **Voiceover Generation** — ElevenLabs TTS with character-level timestamps. Chunked with Request Stitching for consistent voice prosody across long scripts. Voice settings tuned for stability (0.75) with speaker boost enabled.
- **Audio Mastering** — 3-stage post-processing chain: force mono (safety net), `dynaudnorm` (per-frame volume levelling to eliminate chunk-to-chunk differences and tame spikes), then `loudnorm` EBU R128 normalization (-16 LUFS, YouTube target). Output duplicated to stereo for universal playback compatibility.
- **Slot-Based Timing** — Each clip fills its full time slot (speech + silence gap), keeping video in sync with audio.
- **Timeline Assembly** — AI generates a structured JSON Edit Decision List (EDL) with trim points and transitions. Batched processing (25 segments/batch) for large videos.
- **FFmpeg-Direct Rendering** — All video processing (trim, scale, crop, speed-adjust, overlay composite, concat, audio overlay) uses direct FFmpeg subprocess calls for speed and memory efficiency. No MoviePy rendering.
- **Per-Script Organization** — Each script gets its own workspace folder with clips, audio, overlays, credits, output, and debug files.
- **Auto-Versioning** — Re-running the same script creates v2, v3, etc.
- **Checkpoint/Resume** — Completed pipeline stages are cached. Re-runs skip finished stages automatically.
- **Rendering Quality Options** — `--quality draft` (ultrafast) for iteration, `--quality final` (medium) for production.
- **Pexels Attribution** — Automatic `credits.txt` generation with videographer details.

### What's Been Tested

| Test | Script | Segments | Duration | Result |
|------|--------|----------|----------|--------|
| Short-form | `deep_thoughts_01.txt` | 20 | ~2 min | Pass |
| Mid-form | `deep_thoughts_02.txt` | ~80 | ~13 min | Pass |
| Mid-form (FFmpeg-direct) | `deep_thoughts_03.txt` | ~60 | ~9 min | Pass (~70s render) |
| **Long-form (production test)** | `deep_thoughts_04.txt` | **588** | **~62 min** | **Pass (~71 min total pipeline)** |

---

## Pipeline Stages

### 1. Script Analysis

An AI agent ingests the raw script and breaks it into **visual segments**. For large scripts (10K+ chars), the text is split into ~5K-char chunks at paragraph boundaries and each chunk is processed in a separate LLM call with retry logic. Segments are renumbered sequentially after merging.

For each segment, the agent extracts:
- **Key visual concepts** (e.g., "person lying awake in dark bedroom")
- **Mood / tone** (e.g., contemplative, melancholic, wondrous)
- **Search keywords** optimized for stock footage queries (2-4 phrases per segment)
- **Quote type** — one of `direct_quote`, `statistic`, `source_citation`, or `none`. Only 10-20% of segments are marked for overlays to avoid clutter.
- **Quote text** — concise text to display on screen (when quote_type is not "none")
- **Quote attribution** — source/speaker (when applicable)

### 1.5. Text Overlay Generation

For segments with a non-"none" `quote_type`, Pillow generates a styled transparent PNG overlay at the output resolution (1920x1080 by default). Three visual styles:

- **Direct quote card** — Dark semi-transparent rounded rectangle in the lower-left, with a blue accent line on the left edge, white quote text, and gray attribution below.
- **Statistic callout** — Large bold number centered on screen with a semi-transparent backing rectangle. Context text below.
- **Source citation pill** — Small pill-shaped badge in the lower-right corner with the source name.

Overlays are cached in the `overlays/` subfolder. On resume, existing PNGs are reused.

### 2. Footage Retrieval & Selection

For each segment, queries the **Pexels Video API** using the extracted keywords:
- Scores and ranks results for relevance
- Avoids reusing the same clip across segments
- Downloads the best-matching MP4 and caches it in the project folder
- Retries with broader keywords if initial search returns nothing
- Integrated **sliding-window rate limiter** (200 req/hr) with automatic pause and resume
- Captures videographer attribution for Pexels credits
- **Auto-detects orientation** from output resolution — pulls portrait clips for vertical mode, landscape for standard

### 3. Voiceover Generation + Timestamp Extraction

Sends the script to **ElevenLabs TTS** with `with_timestamps` enabled:
- Scripts over 9,500 chars are automatically **chunked** at sentence boundaries
- Uses **Request Stitching** (`previous_request_ids`) for consistent voice across chunks
- Returns narration audio + character-level timing data
- Characters are reconstructed into word boundaries, then mapped to segments
- Each segment gets a **full time slot**: from its `audio_start` to the next segment's `audio_start`
- Post-processing: **3-stage audio mastering** via FFmpeg — force mono, `dynaudnorm` (per-frame levelling), `loudnorm` (EBU R128, -16 LUFS), then output as stereo

### 3.5. Caption Generation (opt-in)

When `--captions` is enabled (or auto-enabled by `--vertical`), generates an SRT subtitle file from the word-level timing data extracted during voiceover generation:
- Words are grouped into readable cues — **8 words per cue** for landscape, **5 words per cue** for vertical (narrower frame)
- Cue boundaries prefer sentence breaks (periods, question marks, exclamation marks) when possible
- The SRT file is saved as `captions.srt` in the project folder and cached across re-runs
- Captions are burned into the final video during the rendering stage

### 4. Timeline Assembly (AI Agent → EDL)

AI agent receives segments with slot timing + footage metadata, outputs a **JSON Edit Decision List**:
- Trim points for each clip (which portion of the source footage to use)
- Transition types (cut or crossfade)
- Clip durations matched to slot durations (not just speech durations)
- Large segment counts (30+) are processed in **batches of 25** to avoid LLM output truncation

### 5. Video Assembly & Rendering (FFmpeg-Direct)

Direct FFmpeg subprocess calls process each clip individually:
- **Per-clip processing**: trim, speed-adjust, scale, crop to output resolution, strip audio, encode to temp MP4. Resolution adapts to vertical (1080x1920) or landscape (1920x1080) mode.
- **Text overlay compositing** *(opt-in)*: If a clip has an overlay PNG, FFmpeg composites it on top with a fade-in / fade-out animation using the `overlay` filter and time-dependent `colorchannelmixer` alpha
- **Concat**: All temp clips joined via FFmpeg concat demuxer (`-c copy`, no re-encoding)
- **Audio overlay**: Narration audio overlaid onto silent video (`-c:v copy`, no video re-encode)
- **Caption burn-in** *(opt-in)*: When an SRT file is provided, captions are burned into the video using FFmpeg's `subtitles` filter with ASS styling:
  - **Landscape mode**: Bottom-center, standard font size, full-width margins
  - **Vertical mode**: Lower-third safe zone (~75% down frame), shorter line width, positioned above platform UI buttons. Uses ASS virtual coordinate system (PlayResX=384, PlayResY=288) for consistent positioning across resolutions.
- **Cleanup**: Temp files removed automatically
- **Quality presets**: `draft` (ultrafast, high threads) or `final` (medium preset)

This approach is ~10-20x faster than MoviePy-based rendering and uses minimal memory (one FFmpeg process at a time).

---

## High-Level Architecture

```
Input: Script (.txt file) + flags (--vertical, --captions, --overlays)
  |
  v
+-----------------------------+
|  1. Script Analysis (AI)    |  Chunked for large scripts
|     + quote/citation detect |  Classifies segments needing overlays
+--------+--------------------+
         |
         v
+-----------------------------+
|  1.5 Text Overlay Gen       |  Pillow -> styled transparent PNGs
|       (Pillow, opt-in)      |  (only when --overlays is used)
+--------+--------------------+
         |
         v
+-------------------------+     +--------------------------+
|  2. Footage Retrieval   |     |  3. Voiceover Generation |
|     (Pexels API)        |     |     (ElevenLabs API)     |
|     + Rate Limiter      |     |     + Request Stitching  |
|     + Attribution       |     |     + Audio Mastering     |
|     + Auto-Orientation  |     +-----------+--------------+
+--------+----------------+                 |
         |                                  v
         |                    +----------------------------+
         |                    |  3.5 Caption Gen (opt-in)  |
         |                    |  SRT from word-level timing|
         |                    |  5 words/cue (vert)        |
         |                    |  8 words/cue (landscape)   |
         |                    +-----------+----------------+
         |                                |
         v                                v
+------------------------------------------+
|   4. Timeline Assembly (AI -> EDL)       |  Batched, slot-based timing
|      + overlay path merge (if overlays)  |  Attaches PNGs to EDL entries
+----------------+-------------------------+
                 |
                 v
+------------------------------------------+
|   5. Video Rendering (FFmpeg-Direct)     |  Per-clip + concat + audio
|      + overlay compositing (opt-in)      |  + caption burn-in (opt-in)
|      + caption burn-in (opt-in)          |  Vertical: lower-third safe zone
+------------------------------------------+
  |
  v
Output: workspace/{name}/output/{name}.mp4       (landscape 1920x1080 or vertical 1080x1920)
        workspace/{name}/credits/credits.txt
        workspace/{name}/captions.srt             (when --captions or --vertical)
        workspace/{name}/overlays/*.png            (when --overlays)
```

---

## Key Design Decisions

| Decision | Choice |
|---|---|
| **AI provider** | OpenAI-compatible API (Kimi K2.5 via Moonshot). Provider-agnostic -- swap via `.env`. |
| **Rendering engine** | FFmpeg-direct subprocess calls. MoviePy is a dependency but not used for rendering. |
| **Segment timing** | Slot-based: each clip fills `audio_start -> next segment's audio_start`. Eliminates drift. |
| **Audio handling** | All clip audio muted. Only the narrator voiceover is heard. Mastered via dynaudnorm + loudnorm, output as stereo. |
| **Voice settings** | ElevenLabs stability=0.75, similarity_boost=0.75, use_speaker_boost=True. Reduces whispering artifacts. |
| **Voice selection** | User-configurable `ELEVENLABS_VOICE_ID` in `.env`. |
| **Output format** | 1920x1080 (landscape) or 1080x1920 (vertical via `--vertical`). H.264 MP4, FPS configurable. |
| **Caption positioning** | ASS virtual coordinate system (384x288) for consistent placement. Vertical: lower-third safe zone above platform UI. Landscape: bottom-center. |
| **File organization** | Per-script project folders in `workspace/`. Auto-versioned output. |
| **Long-form support** | Chunking at every stage (script analysis, TTS, EDL generation). Rate limiting for Pexels. |
| **Short-form support** | `--vertical` flag switches resolution, Pexels orientation, enables captions with shorter cues. |
| **Checkpoint/resume** | All intermediate data saved as JSON. Completed stages skipped on re-run. |

---

## Potential Future Features

### High Priority — Full Video Production Workflow

These features close the gap between "pipeline produces a video" and "video is ready to publish." Currently these steps are done manually after each run.

| Feature | Description |
|---|---|
| **Script generation from topic** | Given a topic (e.g. "the Bitcoin crash of February 2026"), use deep research + LLM to generate a complete 20+ minute video script. Currently scripts are written manually or with external AI assistance before being fed to the pipeline. Integrating this step would make the workflow truly end-to-end: topic in, publish-ready video out. |
| **Video title generation** | Auto-generate a CTR-optimized YouTube title based on the script content. The LLM already understands the full script — generating a compelling title is a natural extension. Output saved to the workspace folder alongside the video. |
| **Video description generation** | Auto-generate a YouTube description including a summary, auto-generated timestamps (derived from segment timing data we already have), and Pexels attribution (from the credits.txt we already generate). Currently descriptions are written manually. |
| **Thumbnail prompt generation** | Auto-generate a Midjourney (or similar) prompt for creating a custom thumbnail image based on the script's topic, tone, and key visuals. The pipeline already knows the visual descriptions and mood of every segment — distilling that into a thumbnail prompt is straightforward. |

### Medium Priority — Video Quality & Features

| Feature | Description |
|---|---|
| ~~**Text overlays for quotes/citations**~~ | ~~Stylized on-screen text for direct quotes, statistics, and source citations~~ -- **DONE** (Pillow + FFmpeg, opt-in via `--overlays`) |
| ~~**Subtitle / caption generation**~~ | ~~Burn captions into the video using the timestamp data~~ -- **DONE** (SRT from word-level timing + FFmpeg `subtitles` filter, opt-in via `--captions`, auto-enabled in vertical mode) |
| ~~**Vertical short-form support**~~ | ~~9:16 output for TikTok / Reels / Shorts~~ -- **DONE** (`--vertical` flag: 1080x1920 output, portrait Pexels footage, lower-third caption placement) |
| **Automatic transitions** | Crossfades, dissolves, or other transitions between clips (currently cuts only in practice) |
| **Background music** | Add a subtle ambient track under the narration |
| **GPU-accelerated encoding** | Use NVENC/QSV for faster rendering on supported hardware |

### Lower Priority — Workflow & Tooling

| Feature | Description |
|---|---|
| **Batch processing** | Run multiple scripts in sequence overnight |
| **Web UI** | Simple interface for uploading scripts and downloading videos |
| **Cost estimation** | Log estimated API costs before running, so the user can confirm |
