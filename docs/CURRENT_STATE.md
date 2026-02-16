# script2vid — Current State

> **Last updated:** 2026-02-15
>
> This document is the quick-start briefing for anyone (human or AI) picking up
> this project without prior context. Read this first, then consult `README.md`
> for setup details and `docs/outline.md` for the full technical specification.

---

## What This Is

An automated video production pipeline: **script in → published video out**.

The user provides a text script, a title, and a description. The pipeline handles
everything else: AI script analysis, stock footage retrieval (Pexels), voiceover
generation (ElevenLabs), caption generation, video rendering (FFmpeg), calendar
scheduling, and YouTube upload — all in one command.

---

## What's Built and Working (Phase 1 — Complete)

### Core Pipeline
- Script analysis (AI, chunked for long-form)
- Stock footage retrieval from Pexels (rate-limited, auto-orientation)
- Voiceover generation via ElevenLabs (chunked with Request Stitching, 3-stage audio mastering)
- Text overlays for quotes/statistics/citations (opt-in, Pillow + FFmpeg)
- Closed captions from word-level timing (opt-in, SRT → burned in via FFmpeg)
- Timeline assembly via AI EDL generation (batched for large videos)
- Video rendering via FFmpeg (landscape 1920x1080 or vertical 1080x1920)
- Checkpoint/resume — interrupted runs pick up where they left off
- Auto-versioning — re-runs create v2, v3, etc.

### Publishing & Scheduling
- YouTube Data API v3 integration (upload, schedule, full metadata)
- Release calendar system (per-channel schedules, placeholder slots, status tracking)
- Multi-time calendar support (e.g., 12:00 PM and 8:00 PM daily)
- Calendar CLI (`python -m src.publishing.calendar_manager`)

### Channel System
- Channel-based workspace routing (`channels/<id>/workspace/`)
- Per-channel default settings (`channels/<id>/default_settings.json`)
- Per-channel OAuth tokens (`channels/<id>/youtube_token.json`) — each channel
  authenticates to its own YouTube account independently
- Per-channel content prompts (`channels/<id>/content_prompt.md`) — standardized
  LLM prompt templates for generating on-brand scripts, titles, and descriptions
- The `--channel` flag is the single switch: loads defaults, routes workspace,
  renders, assigns to calendar, uploads to YouTube

### Web UI
- Pipeline view (`/`) — select channel, upload script, enter metadata, start pipeline
  with real-time progress tracking
- Calendar view (`/calendar`) — interactive monthly calendar with per-channel tabs
- Threaded HTTP server at `http://localhost:5555`

### Content Prompt System
- Each channel has a `content_prompt.md` — a standardized "character sheet" that
  defines the channel's creative voice for an LLM
- Standardized sections: Channel Identity, Content Format, Voice & Tone (Do/Don't),
  Script Structure, Title Guidelines, Description Guidelines, Examples
- Currently used manually (copy-paste into external LLM); next step is pipeline integration
- Template available in `channels_example/example_channel/content_prompt.md`

---

## What's Still Manual (inputs the user must provide)

| Input | How it's provided | Future automation |
|-------|-------------------|-------------------|
| **Script** | Written or generated externally, saved as `.txt` in `scripts/` | `content_prompt.md` exists per channel; integrate LLM call into pipeline |
| **Title** | Passed via `--title` flag or entered in web UI | Same — LLM can generate from script + prompt |
| **Description** | Passed via `--description` flag or entered in web UI | Same — LLM can generate from script + prompt |
| **Thumbnail** | Created externally | `thumbnails/` directory exists in workspace; prompt generation is planned |

Everything else (footage, voiceover, captions, rendering, scheduling, uploading) is fully automated.

---

## Architecture

```
src/
├── main.py                    # Orchestrator — runs the full pipeline
├── config.py                  # Settings, API keys, project directories
├── pipeline/                  # Core video production stages
│   ├── script_analyzer.py     #   Stage 1: Script → visual segments
│   ├── text_overlay.py        #   Stage 1.5: Styled text overlay PNGs
│   ├── footage_finder.py      #   Stage 2: Pexels search → download clips
│   ├── voiceover.py           #   Stage 3: ElevenLabs TTS + timestamps
│   ├── captions.py            #   Stage 3.5: SRT caption generation
│   ├── timeline_builder.py    #   Stage 4: AI → Edit Decision List
│   └── video_assembler.py     #   Stage 5: FFmpeg → final MP4
├── publishing/                # YouTube + scheduling
│   ├── publisher.py           #   YouTube Data API v3 (per-channel OAuth)
│   └── calendar_manager.py    #   Release calendar CLI + logic
├── web/                       # Web interfaces
│   ├── calendar_server.py     #   Threaded HTTP server
│   ├── pipeline_runner.py     #   Background job runner for web UI
│   └── static/
│       ├── pipeline.html      #   Pipeline UI
│       └── calendar.html      #   Calendar UI
└── utils/                     # Shared helpers
    ├── llm.py                 #   OpenAI-compatible chat helper
    └── rate_limiter.py        #   Sliding-window API rate limiter
```

### Channel Directory Structure (gitignored — user-specific)

```
channels/
└── <channel_id>/
    ├── default_settings.json      # Pipeline + publishing defaults
    ├── content_prompt.md          # LLM prompt for content generation
    ├── youtube_token.json         # Per-channel OAuth token
    └── workspace/                 # Per-video project folders
        └── <project>/
            ├── clips/             # Downloaded stock footage
            ├── audio/             # Generated voiceover
            ├── overlays/          # Text overlay PNGs
            ├── thumbnails/        # (Future) thumbnail images
            ├── credits/           # Pexels attribution
            ├── output/            # Rendered video(s)
            ├── 1_segments.json    # Checkpoint: script analysis
            ├── 2_segments_with_footage.json
            ├── 3_alignment.json   # Checkpoint: timing
            ├── 3_segments_with_timing.json
            ├── 4_edl.json         # Checkpoint: edit decision list
            └── captions.srt       # Subtitle file
```

---

## Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| LLM provider | OpenAI-compatible (Kimi K2.5 via Moonshot) | Swap via `.env` — provider-agnostic |
| Rendering | FFmpeg subprocess calls (not MoviePy) | 10-20x faster, minimal memory |
| Audio mastering | dynaudnorm → loudnorm → stereo | Eliminates chunk-to-chunk volume differences |
| Per-channel OAuth | Separate `youtube_token.json` per channel | Enables multi-channel publishing to different YouTube accounts |
| Content prompts | Markdown files with standardized sections | Human-readable, LLM-ready, programmatically parseable |
| Channel defaults | JSON files with CLI override | Explicit flags always win over defaults |
| Calendar | JSON file with multi-time support | Simple, no database dependency |
| Web server | Python `http.server` + `ThreadingMixIn` | Lightweight, no extra dependencies |

---

## Roadmap (What's Next)

### Phase 1.5 — Content Generation Integration
- Wire `content_prompt.md` into the pipeline so the LLM auto-generates scripts, titles, and descriptions
- User provides a topic → pipeline reads the channel's content prompt → LLM generates the script → pipeline runs
- This closes the last manual step for short-form content

### Phase 2 — Local API Service
- Expose the pipeline as a FastAPI service with endpoints for each operation
- Enables third-party integrations and the future agent layer

### Phase 3 — Video Understanding
- Ingestion layer for user-uploaded footage (Whisper + scene detection + multimodal LLM)
- See `docs/outline.md` "User-Uploaded Footage: Video Understanding Pipeline" for full design

### Phase 4 — Agentic Editor
- TypeScript agent layer with conversational interface
- See `docs/outline.md` "Long-Term Vision: Agentic Video Editor" for full design

---

## File Reference

| File | Purpose |
|------|---------|
| `README.md` | Setup instructions, CLI usage, full feature documentation |
| `docs/outline.md` | Technical specification, architecture diagrams, future vision |
| `docs/long_form_roadmap.md` | Historical — long-form feature development (complete) |
| `docs/hurdles.md` | Troubleshooting log for past issues and fixes |
| `docs/CURRENT_STATE.md` | This file — quick-start briefing |
| `channels_example/` | Template for new channel setup (tracked in git) |
| `.env.example` | Template for API key configuration |
| `requirements.txt` | Python dependencies with versions |
