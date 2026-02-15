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
| **Cost estimation** | Log estimated API costs before running, so the user can confirm |

---

## Long-Term Vision: Agentic Video Editor

### The Idea

script2vid currently works as a CLI pipeline directed by the user through an external tool (Cursor, terminal). The user writes or requests scripts, chooses flags, runs the pipeline, then comes back to ask for titles, descriptions, thumbnails, and tweaks. The "agent" is really the user + Cursor orchestrating the pipeline.

The long-term evolution is to collapse all of that into a **single conversational product** — an agentic video editor where the user simply talks to an AI agent that understands the project and has direct access to all editing tools.

Think of it like CapCut, but instead of learning where every UI button is, you just type (or speak) what you want:

- *"Make a 60-second vertical short about how money works"*
- *"The intro feels slow — trim the first clip by 2 seconds"*
- *"Swap the footage in segment 4 for something more dramatic"*
- *"Add captions and export for TikTok"*
- *"Give me 5 title options and a thumbnail prompt"*
- *"Schedule this to post tomorrow at 2pm"*

### Why This Is Feasible

The foundation already exists. Our pipeline generates **rich structured data** at every stage that an agent can reason about:

- **Segments JSON** — the agent knows what every section of the video is about, its mood, keywords, and visual description
- **EDL JSON** — the agent knows exactly which footage file is used where, trim points, durations, and transitions
- **Alignment JSON** — the agent knows the exact timing of every word in the narration
- **Captions SRT** — the agent can read and modify subtitle cues
- **Credits** — the agent knows which Pexels clips are used and can attribute them
- **Footage metadata** — the agent knows clip durations, orientations, and sources

This isn't a black-box video file — it's a fully decomposed, inspectable, modifiable project. An agent with access to this data and the ability to call FFmpeg, Pexels, and ElevenLabs can make surgical edits conversationally.

### What the Agent Could Do

| Capability | How |
|---|---|
| **Generate a video from a topic** | Research + script generation + full pipeline execution |
| **Edit an existing video** | Modify the EDL, re-render specific clips, swap footage, adjust timing |
| **Preview changes** | Re-render only the affected section, show before/after |
| **Adjust narration** | Re-generate specific TTS chunks, adjust voice settings per section |
| **Manage captions** | Edit SRT cues, reposition, restyle |
| **Generate metadata** | Titles, descriptions, timestamps, thumbnail prompts — all from project data |
| **Publish** | Schedule uploads to YouTube, TikTok, Instagram via their APIs |
| **Multi-project awareness** | "Use the same narrator voice as last week's video" |

### Architecture Implications

The current Python pipeline would remain as the **backend engine** — it already handles FFmpeg, Pexels, ElevenLabs, and LLM calls well. What changes is what sits on top of it:

```
Current architecture:
  User -> Cursor/Terminal -> CLI flags -> Python pipeline -> Output

Agentic editor architecture:
  User -> Conversational UI -> Agent orchestrator -> Python pipeline -> Output
              |                      |
              |                      +-- Project state (JSON data)
              |                      +-- Tool registry (FFmpeg, APIs, publish)
              |                      +-- Memory (cross-project, user preferences)
              +-- Real-time preview
              +-- Timeline visualization
              +-- Export/publish controls
```

**Technology choices for the application layer:**

- **TypeScript** for the agent orchestrator, UI, and real-time features. This is where frameworks like OpenClaw and ElizaOS chose TypeScript — conversational interfaces, WebSocket communication, event-driven architecture, and browser-based UI are all TypeScript's strength.
- **Electron or Tauri** for a cross-platform desktop app (or a web app for browser-based access).
- **React** for the timeline/canvas visualization and conversational interface.
- **Python backend** stays as-is — exposed as a local API service that the TypeScript agent layer calls into.

### User-Uploaded Footage: Video Understanding Pipeline

A key capability for the agentic editor is letting users bring their own footage — not just Pexels stock clips. The challenge: Pexels footage comes with metadata (tags, descriptions, durations) that our pipeline already uses. User-uploaded footage is a black box. We need an **ingestion layer** that extracts rich contextual data so the agent can reason about user footage the same way it reasons about Pexels clips.

**The two knowledge gaps to fill:**

**Audio context (what is being said / heard):**
- **Whisper** (OpenAI, open-source) — runs locally, free, produces timestamped word-level transcriptions. Available via `openai-whisper` Python package or `whisper.cpp` for speed. This handles dialogue, narration, and any spoken content.
- **Audio classification** — FFmpeg `silencedetect` for gaps, `librosa` for music vs. speech vs. effects detection. Knowing *when* things are said vs. silent vs. scored with music is valuable editing context.

**Visual context (what is being shown):**
- **Scene detection** — `PySceneDetect` or FFmpeg's `select='gt(scene,0.3)'` filter splits video into distinct scenes at cut points, giving natural segment boundaries.
- **Frame extraction** — FFmpeg pulls 1-2 keyframes per detected scene. Cheap and fast.
- **Multimodal LLM** — send extracted keyframes to a vision-capable model (GPT-4V, Claude Vision, Gemini) to describe what's happening: characters, actions, settings, emotions, art style ("anime fight scene on a rooftop at sunset"), etc. This is the richest source of visual understanding.
- **OCR** — Tesseract (free, local) for any on-screen text: subtitles, signs, UI elements, title cards.

**Deep context (what it actually IS, not just what it looks like):**

Basic visual description tells you *what something looks like*. Deep context tells you *what it is*. "Black and white footage of an airship on fire" vs. "The Hindenburg disaster, May 6, 1937, Lakehurst, New Jersey — killed 36 people, ended the era of commercial airship travel." An agent editing a documentary needs the second kind of knowledge.

This is achieved through a **multi-pass enrichment approach:**

1. **Recognition-first prompting** — The multimodal LLM is prompted not just to describe, but to *identify*: "What specific historical event, public figure, cultural work, or real-world context do you recognize? If this is from a known film, anime, documentary, or event, name it. Be precise — what IS this, not just what it looks like." Modern vision models have deep world knowledge and can recognize famous events, landmarks, public figures, anime/film scenes, etc.

2. **Transcript-informed visual enrichment** — The audio track often names what the video shows. A second LLM pass combines the transcript AND the visual descriptions: "The narrator says 'the 1937 disaster that changed aviation forever' and the frame shows an airship on fire — identify the specific event and provide full context." The transcript gives the LLM the hint it needs to go from vague to specific.

3. **Web search augmentation** — For anything the LLM is uncertain about, an automated web search fills the gap. The LLM describes a frame as "1960s protest march, Washington DC" → auto-search "1960s protest march Washington DC" → March on Washington, August 28, 1963, MLK's "I Have a Dream" speech. This is the same deep-research loop we already use for script writing, applied to video understanding.

This distinction matters because it determines whether the agent can *intelligently* use footage. Without deep context, the agent can only match by visual similarity ("find me footage of fire"). With it, the agent understands meaning ("find me footage of a pivotal historical disaster for the section about things that changed the world overnight").

**The ingestion pipeline (3-pass):**

```
User uploads video to media_library/
       |
       v
  PASS 1: Extraction
  Scene Detection (PySceneDetect / FFmpeg) -> scene boundaries
  Frame Extraction (FFmpeg) -> 1-2 keyframes per scene
  Audio Transcription (Whisper, local/free) -> timestamped transcript
  OCR (Tesseract) -> on-screen text
       |
       v
  PASS 2: Recognition + Description
  Multimodal LLM receives keyframes + transcript together
  Prompted for identification, not just description:
    - What specific event/person/place/work is this?
    - Historical significance, dates, key figures
    - Cultural context (film, anime, documentary, news footage)
    - Emotional tone and mood
       |
       v
  PASS 3: Deep Enrichment (when needed)
  For low-confidence identifications or unknown content:
    - Web search augmentation (visual clues + transcript -> search -> context)
    - Cross-reference with transcript ("narrator mentions X, frame shows Y")
    - Fill in dates, significance, related events
       |
       v
  Cached metadata JSON per file
  Agent has full searchable knowledge of the footage
```

**Output format** — rich metadata with both surface description and deep context:

```json
{
  "scenes": [
    {
      "scene_id": 1,
      "start_time": 0.0,
      "end_time": 4.2,
      "visual_description": "Black and white footage of a large airship engulfed in flames, people running on the ground",
      "identification": {
        "event": "Hindenburg disaster",
        "date": "May 6, 1937",
        "location": "Lakehurst Naval Air Station, New Jersey",
        "significance": "Killed 36 people, ended commercial airship era, first major disaster broadcast live on radio",
        "key_figures": ["Herbert Morrison (radio reporter)"],
        "source_type": "historical_footage",
        "confidence": "high"
      },
      "mood": "catastrophic, historic",
      "transcript": "Oh, the humanity!",
      "transcript_start": 0.5,
      "transcript_end": 1.8,
      "on_screen_text": null,
      "source_file": "media_library/hindenburg_footage.mp4"
    }
  ]
}
```

Compared to Pexels metadata (which gives us tags like `["airship", "fire", "disaster"]`), this is orders of magnitude richer. The agent doesn't just know what the footage looks like — it knows what it *means*, when it happened, why it matters, and how it connects to other concepts. This enables intelligent editing decisions that a tag-based system never could.

Once ingested, user footage becomes first-class data — the agent can search it by content, context, historical period, or meaning ("find me footage from the 1930s about technological failure"), use it in the EDL, trim it precisely, and reference it in editing conversations. The metadata is cached after the first analysis, so the library grows over time without repeated processing.

**Key tools (all free or open-source):**
- Whisper (speech-to-text, local)
- PySceneDetect (scene boundary detection)
- FFmpeg (frame extraction, audio analysis)
- Tesseract (OCR)
- Multimodal LLM (visual recognition + deep context — uses existing LLM infrastructure if the provider supports vision)
- Web search API (enrichment pass — for context the LLM can't identify from the frame alone)

### Competitive Landscape

This would position script2vid differently from existing tools:

- **CapCut / Premiere / DaVinci** — powerful but require learning complex UIs. Not conversational.
- **Descript** — closest existing product (transcript-based editing), but not fully agentic. Still UI-driven.
- **Runway / Pika** — AI video generation, but focused on generating footage, not assembling/editing complete videos from scripts.
- **script2vid as agentic editor** — conversation-first, knows the full project structure, can generate AND edit AND publish. Understands both stock footage and user-uploaded content through structured metadata. The user never needs to learn a UI.

### Development Phases

1. **Phase 1 (current):** CLI pipeline with rich structured data. All the building blocks exist.
2. **Phase 2:** Expose the pipeline as a local API service (Python FastAPI or similar). Add endpoints for each operation: generate, edit segment, swap footage, re-render section, etc.
3. **Phase 3:** Build the video understanding / ingestion layer for user-uploaded footage (Whisper + scene detection + multimodal LLM). Users can bring their own clips and the system understands them.
4. **Phase 4:** Build the TypeScript agent layer. Conversational interface, tool registry, project state management. The agent can call pipeline API endpoints as tools.
5. **Phase 5:** Add the visual layer. Timeline preview, real-time rendering feedback, canvas for thumbnail editing.
6. **Phase 6:** Publishing integrations. YouTube, TikTok, Instagram scheduling and upload via their APIs.
