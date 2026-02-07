# script2vid — Project Outline

## Overview

script2vid is an automated video production pipeline that takes a written script as input and produces a fully assembled video with narrated voiceover and relevant stock footage — no manual editing required.

---

## Pipeline Stages

### 1. Script Analysis

An AI agent ingests the raw script and breaks it into **timed segments** — logical chunks based on topic, scene, or idea. For each segment, the agent extracts:

- **Key visual concepts** (e.g., "city skyline at night", "person typing on laptop")
- **Mood / tone** (e.g., dramatic, calm, energetic)
- **Search keywords** optimized for stock footage queries

This is the foundation — everything downstream depends on how well the script is decomposed into meaningful, visual segments.

### 2. Footage Retrieval & Selection

For each segment, the AI agent queries the **Pexels Video API** using the extracted keywords. It then:

- Parses the returned metadata (tags, duration, resolution, preview images)
- **Scores and ranks** each candidate clip for relevance to the segment's visual description
- Selects the best-matching clip(s), factoring in quality, duration fit, and variety (avoiding repetition across the video)
- Downloads the selected MP4 files and caches them locally

### 3. Voiceover Generation + Timestamp Extraction

The full script is sent to the **ElevenLabs Text-to-Speech API** with the `with_timestamps` option enabled. This returns two things at once:

- The generated **narration audio file**
- **Word-level alignment data** — the exact start and end time of every word in the audio

This is the timing backbone of the entire pipeline. Since we know which words belong to which script segment (from step 1), we can map each segment to a precise time range in the audio (e.g., segment 1 = 0.0s–4.2s, segment 2 = 4.2s–9.7s).

Additional considerations:
- Voice selection (choose a voice that fits the script's tone)
- The generated audio defines the **master timeline** — its duration and pacing drive everything else
- **Fallback:** If ElevenLabs timestamps are unavailable or insufficient, run the audio through **Whisper** (OpenAI's speech recognition) for forced alignment as a backup

### 4. Timeline Assembly (AI Agent → Edit Decision List)

This step is split into two sub-stages to separate creative judgment from mechanical execution:

**4a. AI Agent generates an Edit Decision List (EDL):**
- The agent receives: script segments, their time ranges, and footage clip metadata
- It reasons about which portion of each clip best fits each segment's visual concept
- It decides transition types (hard cut, crossfade) and handles edge cases (clips too short/long)
- It outputs a **structured JSON EDL** — a precise blueprint specifying trim points, ordering, and transitions
- See `video_assembly_approach.md` for full EDL format

**4b. Deterministic code executes the EDL:**
- A Python script reads the EDL and uses **MoviePy 2.x** to execute it
- Clips are trimmed, concatenated, and sequenced exactly as specified
- No AI in this sub-stage — same EDL always produces the same output
- This separation makes the pipeline **debuggable** (inspect the EDL) and **reliable** (code doesn't hallucinate)

### 5. Video Rendering

MoviePy (built on FFmpeg) handles the final composition:

- Overlay the narration audio onto the sequenced footage timeline
- Apply transitions specified in the EDL (crossfades, cuts)
- Apply any final adjustments (audio levels, padding, intro/outro)
- Render to MP4 (H.264)

---

## High-Level Architecture

```
Input: Script (text)
  │
  ▼
┌─────────────────────┐
│  1. Script Analysis  │  AI agent decomposes script into visual segments
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
│          4. Timeline Assembly            │  AI agent aligns clips to audio
└────────────────┬─────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────┐
│          5. Video Rendering              │  Compose final MP4 output
└──────────────────────────────────────────┘
  │
  ▼
Output: Finished video file
```

---

## Key Design Decisions

| Decision | Status | Choice |
|---|---|---|
| **AI provider** | Decided | OpenAI-compatible API format (supports Kimi 2.5, OpenAI, and others via base URL + key). Provider-agnostic. |
| **Video editing library** | Decided | MoviePy 2.x (Python, built on FFmpeg). AI outputs JSON EDL; MoviePy executes it. |
| **Segment timing** | Decided | ElevenLabs `with_timestamps` for character-level timing, reconstructed to word/segment boundaries. Whisper as fallback. |
| **Voice selection** | Decided | User-configurable via config. Pass a voice ID to ElevenLabs. Default provided. |
| **Output format** | Decided | 1080p MP4 (H.264) by default. Resolution configurable. Footage resized to match. |
