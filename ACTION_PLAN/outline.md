# script2vid — Project Outline

## Overview

script2vid is an automated video production pipeline that takes a written script as input and produces a fully assembled video with narrated voiceover and relevant stock footage — no manual editing required.

---

## Current Status: Production-Ready

The pipeline is **fully built, tested, and production-ready** for both short-form and long-form content. It has been successfully tested on videos ranging from 2 minutes to over 1 hour (62 minutes).

### What's Been Built

- **Script Analysis** — AI decomposes script into visual segments with keywords, mood, and descriptions. Chunked processing for large scripts (5K chars/chunk with retry logic).
- **Footage Retrieval** — Searches Pexels, scores/ranks results, downloads best matches, avoids repeats. Integrated rate limiter (200 req/hr sliding window). Captures Pexels attribution for credits.
- **Voiceover Generation** — ElevenLabs TTS with character-level timestamps. Chunked with Request Stitching for consistent voice prosody across long scripts.
- **Audio Mastering** — 3-stage post-processing chain: force mono (safety net), `dynaudnorm` (per-frame volume levelling to eliminate chunk-to-chunk differences and tame spikes), then `loudnorm` EBU R128 normalization (-16 LUFS, YouTube target). Output duplicated to stereo for universal playback compatibility.
- **Slot-Based Timing** — Each clip fills its full time slot (speech + silence gap), keeping video in sync with audio.
- **Timeline Assembly** — AI generates a structured JSON Edit Decision List (EDL) with trim points and transitions. Batched processing (25 segments/batch) for large videos.
- **FFmpeg-Direct Rendering** — All video processing (trim, scale, crop, speed-adjust, concat, audio overlay) uses direct FFmpeg subprocess calls for speed and memory efficiency. No MoviePy rendering.
- **Per-Script Organization** — Each script gets its own workspace folder with clips, audio, credits, output, and debug files.
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

### 2. Footage Retrieval & Selection

For each segment, queries the **Pexels Video API** using the extracted keywords:
- Scores and ranks results for relevance
- Avoids reusing the same clip across segments
- Downloads the best-matching MP4 and caches it in the project folder
- Retries with broader keywords if initial search returns nothing
- Integrated **sliding-window rate limiter** (200 req/hr) with automatic pause and resume
- Captures videographer attribution for Pexels credits

### 3. Voiceover Generation + Timestamp Extraction

Sends the script to **ElevenLabs TTS** with `with_timestamps` enabled:
- Scripts over 9,500 chars are automatically **chunked** at sentence boundaries
- Uses **Request Stitching** (`previous_request_ids`) for consistent voice across chunks
- Returns narration audio + character-level timing data
- Characters are reconstructed into word boundaries, then mapped to segments
- Each segment gets a **full time slot**: from its `audio_start` to the next segment's `audio_start`
- Post-processing: **3-stage audio mastering** via FFmpeg — force mono, `dynaudnorm` (per-frame levelling), `loudnorm` (EBU R128, -16 LUFS), then output as stereo

### 4. Timeline Assembly (AI Agent → EDL)

AI agent receives segments with slot timing + footage metadata, outputs a **JSON Edit Decision List**:
- Trim points for each clip (which portion of the source footage to use)
- Transition types (cut or crossfade)
- Clip durations matched to slot durations (not just speech durations)
- Large segment counts (30+) are processed in **batches of 25** to avoid LLM output truncation

### 5. Video Assembly & Rendering (FFmpeg-Direct)

Direct FFmpeg subprocess calls process each clip individually:
- **Per-clip processing**: trim, speed-adjust, scale, crop to 1080p, strip audio, encode to temp MP4
- **Concat**: All temp clips joined via FFmpeg concat demuxer (`-c copy`, no re-encoding)
- **Audio overlay**: Narration audio overlaid onto silent video (`-c:v copy`, no video re-encode)
- **Cleanup**: Temp files removed automatically
- **Quality presets**: `draft` (ultrafast, high threads) or `final` (medium preset)

This approach is ~10-20x faster than MoviePy-based rendering and uses minimal memory (one FFmpeg process at a time).

---

## High-Level Architecture

```
Input: Script (.txt file)
  │
  ▼
┌─────────────────────────────┐
│  1. Script Analysis (AI)    │  Chunked for large scripts
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────┐     ┌──────────────────────────┐
│  2. Footage Retrieval   │     │  3. Voiceover Generation  │
│     (Pexels API)        │     │     (ElevenLabs API)      │
│     + Rate Limiter      │     │     + Request Stitching   │
│     + Attribution       │     │     + Audio Normalization  │
└────────┬────────────────┘     └────────┬─────────────────┘
         │                               │
         ▼                               ▼
┌──────────────────────────────────────────┐
│   4. Timeline Assembly (AI → EDL)        │  Batched, slot-based timing
└────────────────┬─────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────┐
│   5. Video Rendering (FFmpeg-Direct)     │  Per-clip + concat + audio
└──────────────────────────────────────────┘
  │
  ▼
Output: workspace/{script_name}/output/{script_name}.mp4
        workspace/{script_name}/credits/credits.txt
```

---

## Key Design Decisions

| Decision | Choice |
|---|---|
| **AI provider** | OpenAI-compatible API (Kimi K2.5 via Moonshot). Provider-agnostic — swap via `.env`. |
| **Rendering engine** | FFmpeg-direct subprocess calls. MoviePy is a dependency but not used for rendering. |
| **Segment timing** | Slot-based: each clip fills `audio_start → next segment's audio_start`. Eliminates drift. |
| **Audio handling** | All clip audio muted. Only the narrator voiceover is heard. Mastered via dynaudnorm + loudnorm, output as stereo. |
| **Voice selection** | User-configurable `ELEVENLABS_VOICE_ID` in `.env`. |
| **Output format** | 1080p MP4 (H.264) by default. Resolution/FPS configurable. |
| **File organization** | Per-script project folders in `workspace/`. Auto-versioned output. |
| **Long-form support** | Chunking at every stage (script analysis, TTS, EDL generation). Rate limiting for Pexels. |
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
| **Automatic transitions** | Crossfades, dissolves, or other transitions between clips (currently cuts only in practice) |
| **Background music** | Add a subtle ambient track under the narration |
| **Subtitle generation** | Burn captions into the video using the timestamp data we already have |
| **GPU-accelerated encoding** | Use NVENC/QSV for faster rendering on supported hardware |

### Lower Priority — Workflow & Tooling

| Feature | Description |
|---|---|
| **Batch processing** | Run multiple scripts in sequence overnight |
| **Web UI** | Simple interface for uploading scripts and downloading videos |
| **Cost estimation** | Log estimated API costs before running, so the user can confirm |
