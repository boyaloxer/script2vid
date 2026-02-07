# script2vid — Project Outline

## Overview

script2vid is an automated video production pipeline that takes a written script as input and produces a fully assembled video with narrated voiceover and relevant stock footage — no manual editing required.

---

## Current Status: Working Prototype

The core pipeline is **built and tested**. It successfully produces short-form videos (~2 minutes) end-to-end with accurate audio/video sync. The next milestone is scaling to long-form content (1+ hours).

### What's Been Built

- **Script Analysis** — AI decomposes script into visual segments with keywords, mood, and descriptions
- **Footage Retrieval** — Searches Pexels, scores/ranks results, downloads best matches, avoids repeats
- **Voiceover Generation** — ElevenLabs TTS with character-level timestamps, reconstructed to word/segment boundaries
- **Slot-Based Timing** — Each clip fills its full time slot (speech + silence gap), keeping video in sync with audio
- **Timeline Assembly** — AI generates a structured JSON Edit Decision List (EDL) with trim points and transitions
- **Video Rendering** — MoviePy executes the EDL, mutes clip audio (narrator only), renders to 1080p MP4
- **Per-Script Organization** — Each script gets its own workspace folder with clips, audio, output, and debug files
- **Auto-Versioning** — Re-running the same script creates v2, v3, etc. so you can compare and pick the best

### What's Been Tested

- 2-minute "Deep Thoughts For Sleep" script: 20 segments, 20 clips downloaded, audio synced, rendered successfully
- Timing fix verified: slot-based duration eliminated audio drift issue from initial prototype

---

## Pipeline Stages

### 1. Script Analysis

An AI agent ingests the raw script and breaks it into **visual segments** — logical chunks based on topic, scene, or idea. For each segment, the agent extracts:

- **Key visual concepts** (e.g., "person lying awake in dark bedroom")
- **Mood / tone** (e.g., contemplative, melancholic, wondrous)
- **Search keywords** optimized for stock footage queries (2-4 phrases per segment)

### 2. Footage Retrieval & Selection

For each segment, queries the **Pexels Video API** using the extracted keywords:

- Scores and ranks results for relevance
- Avoids reusing the same clip across segments
- Downloads the best-matching MP4 and caches it in the project folder
- Retries with broader keywords if initial search returns nothing

### 3. Voiceover Generation + Timestamp Extraction

Sends the full script to **ElevenLabs TTS** with `with_timestamps` enabled:

- Returns the narration audio + character-level timing data
- Characters are reconstructed into word boundaries
- Each segment is mapped to a **full time slot**: from its `audio_start` to the next segment's `audio_start`
- This slot-based timing ensures clips fill the silence gaps and stay in sync

### 4. Timeline Assembly (AI Agent → EDL)

**4a.** AI agent receives segments with slot timing + footage metadata, outputs a **JSON Edit Decision List**:
- Trim points for each clip (which portion of the source footage to use)
- Transition types (cut or crossfade)
- Clip durations matched to slot durations (not just speech durations)

**4b.** Deterministic Python code executes the EDL using MoviePy:
- Clips are trimmed, stripped of audio, resized to 1080p, and concatenated
- Narration audio is overlaid as the only audio track
- Same EDL always produces the same output

### 5. Video Rendering

MoviePy (built on FFmpeg) renders the final composition to MP4 (H.264 video, AAC audio).

---

## High-Level Architecture

```
Input: Script (.txt file)
  │
  ▼
┌─────────────────────┐
│  1. Script Analysis  │  AI → visual segments + keywords
└────────┬────────────┘
         │
         ▼
┌─────────────────────────┐     ┌──────────────────────────┐
│  2. Footage Retrieval   │     │  3. Voiceover Generation  │
│     (Pexels API)        │     │     (ElevenLabs API)      │
└────────┬────────────────┘     └────────┬─────────────────┘
         │                               │
         ▼                               ▼
┌──────────────────────────────────────────┐
│       4. Timeline Assembly (AI → EDL)    │  Slot-based timing
└────────────────┬─────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────┐
│       5. Video Rendering (MoviePy)       │  Narrator audio only
└──────────────────────────────────────────┘
  │
  ▼
Output: workspace/{script_name}/output/{script_name}.mp4
```

---

## Key Design Decisions

| Decision | Status | Choice |
|---|---|---|
| **AI provider** | Decided | OpenAI-compatible API (Kimi K2.5 via Moonshot). Provider-agnostic — swap via `.env`. |
| **Video editing library** | Decided | MoviePy 2.x (Python, built on FFmpeg). AI outputs JSON EDL; MoviePy executes it. |
| **Segment timing** | Decided | Slot-based: each clip fills `audio_start → next segment's audio_start`. Eliminates drift. |
| **Audio handling** | Decided | All clip audio muted. Only the narrator voiceover is heard. |
| **Voice selection** | Decided | User-configurable `ELEVENLABS_VOICE_ID` in `.env`. |
| **Output format** | Decided | 1080p MP4 (H.264) by default. Resolution/FPS configurable. |
| **File organization** | Decided | Per-script project folders in `workspace/`. Auto-versioned output (v2, v3, etc.). |

---

## Next Milestone: Long-Form Content (1+ Hour Videos)

See `long_form_roadmap.md` for the full plan. Key items (refined after deep research):

1. **Immediate fixes** — Add `max_tokens` to LLM calls (prevents JSON truncation), make ElevenLabs model configurable in `.env`
2. **ElevenLabs chunked TTS with Request Stitching** — Split long scripts into ~10K-char chunks, use `previous_request_ids` to maintain voice consistency across chunks
3. **Pexels rate limiter** — Sliding-window tracker to auto-pause at 180 req/hour; also can request unlimited access for free
4. **LLM batched EDL generation** — Process 20–30 segments per batch to avoid output truncation
5. **Checkpoint/resume** — Skip completed stages on re-run so failures don't waste hours of API calls
6. **Render optimization** — Faster FFmpeg presets, auto-detect CPU threads, optional GPU encoding
7. **Memory-efficient rendering** — Batch-render + FFmpeg concat for 100+ clip videos
