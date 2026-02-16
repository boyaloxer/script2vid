# script2vid

Turn a written script into a fully assembled video with AI-selected stock footage and narrated voiceover — no manual editing required. Supports both short-form vertical content (Shorts/Reels/TikTok) and long-form videos (tested up to 1+ hour).

## What You Provide

All you need is **3 API keys** and **a script**. Everything else is automated.

| What | Where to get it | Cost |
|---|---|---|
| **LLM API key** | Any OpenAI-compatible provider: [Moonshot / Kimi K2.5](https://platform.moonshot.ai/), [OpenAI](https://platform.openai.com/), etc. | Varies by provider |
| **Pexels API key** | [pexels.com](https://www.pexels.com/api/) — sign up and get a key | Free |
| **ElevenLabs API key** | [elevenlabs.io](https://elevenlabs.io) — sign up and get a key from your dashboard | Free tier available |
| **Your script** | A plain text file (`.txt`) in the `scripts/` folder | — |

**What you do NOT need to provide:**
- No video footage — searched and downloaded automatically from Pexels
- No audio files — generated automatically by ElevenLabs
- No editing decisions — the AI handles clip selection, trimming, and sequencing
- No video editing software — FFmpeg renders the final video

## How It Works

1. **Script Analysis** — An AI breaks your script into visual segments with search keywords and quote/citation classification (chunked for large scripts)
2. **Text Overlays** *(opt-in)* — Pillow generates styled PNG overlays for direct quotes, statistics, and source citations
3. **Footage Retrieval** — Searches Pexels for stock footage matching each segment (rate-limited, with automatic pause/resume). Automatically pulls portrait-oriented clips in vertical mode
4. **Voiceover Generation** — ElevenLabs generates narration audio with character-level timestamps (chunked with Request Stitching for long scripts, then mastered via dynaudnorm + loudnorm for consistent volume)
5. **Caption Generation** *(opt-in)* — Generates SRT subtitles from word-level timing data, with shorter cues for vertical videos
6. **Timeline Assembly** — An AI agent creates an Edit Decision List (EDL) mapping clips to the audio timeline, using slot-based timing so footage stays in sync with narration (batched for large segment counts)
7. **Video Rendering** — FFmpeg processes each clip individually, concatenates them, overlays the narration audio, and optionally burns in captions. Vertical mode positions captions in the lower-third safe zone to avoid platform UI overlap
8. **YouTube Publishing** *(opt-in)* — Uploads the rendered video to YouTube via the Data API v3, with optional scheduled publishing

All intermediate data is saved as checkpoints. If the pipeline is interrupted, re-running picks up where it left off.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

This installs: `moviepy` (audio probing), `requests` (API calls), `python-dotenv` (config), `Pillow` (text overlays), `google-api-python-client` + `google-auth-oauthlib` (YouTube API), `tzdata` (timezone support on Windows).

FFmpeg is also required (used directly for all video rendering). Install it if you don't have it:
- **Windows:** `winget install FFmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html)
- **Mac:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`

### 2. Configure API keys

```bash
cp .env.example .env
```

Then open `.env` and fill in your keys:

```env
# LLM (any OpenAI-compatible API)
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.moonshot.ai/v1    # change if using a different provider
LLM_MODEL=kimi-k2.5                         # change to match your provider's model name

# Pexels
PEXELS_API_KEY=your_key_here

# ElevenLabs
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # optional — pick a voice from ElevenLabs
```

### 3. (Optional) YouTube API setup

To enable automated YouTube uploads and scheduled publishing:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the **YouTube Data API v3**
3. Create **OAuth 2.0 Desktop App** credentials
4. Download as `client_secrets.json` and place it in the project root
5. On first upload for each channel, a browser window opens for authorization — **select the correct YouTube channel when prompted**
6. The token is saved per-channel at `channels/<id>/youtube_token.json` so each channel can authenticate to a different YouTube account
7. All channels share the same `client_secrets.json` (your Google Cloud OAuth app), but each stores its own token

### 4. (Optional) Channel setup

To use the `--channel` flag for one-step scheduled publishing, set up your channels:

```bash
# Add a channel to the calendar (supports multiple daily times, comma-separated)
python -m src.publishing.calendar_manager add-channel \
  --id deep_thoughts \
  --name "Deep Thoughts For Zen" \
  --days mon,tue,wed,thu,fri,sat,sun \
  --time "12:00,20:00" \
  --timezone America/New_York

# Generate placeholder slots
python -m src.publishing.calendar_manager generate --weeks 4
```

Each channel has its own directory under `channels/<id>/` containing:

- **`default_settings.json`** — pipeline and publishing defaults (vertical, captions, quality, publish, category, tags, privacy, etc.)
- **`content_prompt.md`** — LLM prompt template for generating scripts, titles, and descriptions consistent with the channel's voice and style. This is the channel's "character sheet" — designed to be sent to any LLM to produce on-brand content. Standardized sections: Channel Identity, Content Format, Voice & Tone, Script Structure, Title/Description Guidelines, and Examples.
- **`youtube_token.json`** — per-channel OAuth token (auto-created on first publish)
- **`workspace/`** — per-video project folders (auto-created by the pipeline)

See `channels_example/` for setup instructions and templates.

## Usage

Place your script in the `scripts/` folder as a `.txt` file, then run:

```bash
python -u -m src scripts/my_video.txt
```

The `-u` flag ensures real-time console output (recommended).

### Options

| Flag | Description |
|---|---|
| `--quality draft` | Fast rendering (ultrafast FFmpeg preset) — good for previewing |
| `--quality final` | Higher quality rendering (medium FFmpeg preset) — use for uploads |
| `--fresh` | Ignore checkpoints and re-run all stages from scratch |
| `--captions` | Burn closed captions into the video, synced to the narrator's speech |
| `--overlays` | Enable text overlays for quotes, statistics, and citations (experimental) |
| `--vertical` | Render in vertical 9:16 format (1080×1920) for TikTok / Reels / YouTube Shorts |
| `--channel ID` | The single switch: routes workspace, applies channel defaults, renders, assigns to calendar, uploads to YouTube with scheduled time |
| `--publish` | Upload the rendered video to YouTube after the pipeline completes |
| `--schedule ISO` | Schedule YouTube publish time (ISO 8601). Implies `--publish` |
| `--title` | YouTube video title (defaults to project name) |
| `--description` | YouTube video description |
| `--tags` | Comma-separated YouTube tags |
| `--category` | YouTube category: `people`, `education`, `entertainment`, `news`, etc. |
| `--privacy` | YouTube privacy: `public`, `private`, `unlisted` (default: `private`) |

### Examples

```bash
# Draft quality for quick preview
python -u -m src scripts/deep_thoughts_01.txt --quality draft

# Final quality landscape video with captions
python -u -m src scripts/my_video.txt --quality final --captions

# Vertical short-form for YouTube Shorts
python -u -m src scripts/my_short.txt --vertical --captions

# One-step channel workflow: render + assign to calendar + upload to YouTube
python -u -m src scripts/my_short.txt --channel deep_thoughts

# Manual YouTube upload with scheduling
python -u -m src scripts/my_video.txt --publish --schedule 2026-03-01T14:00:00Z --title "My Video"
```

### The `--channel` Flag

The `--channel` flag is the primary way to produce and publish videos. When used:

1. **Workspace** routes to `channels/<id>/workspace/` for clean per-channel organization
2. **Default settings** load from `channels/<id>/default_settings.json` (vertical, captions, quality, etc.)
3. **Pipeline** runs with those merged settings (explicit CLI flags always override)
4. **Calendar** auto-assigns the rendered video to the next open slot for that channel
5. **YouTube** uploads the video as private, scheduled to go public at the slot's time
6. **Calendar** updates the slot status from `placeholder` → `uploaded`

### Checkpoint / Resume

Each pipeline stage saves its output as a JSON file. If interrupted, simply re-run the same command. Completed stages are detected and skipped:

```
STAGE 1: Script Analysis [CACHED — skipping]
STAGE 2: Footage Retrieval [CACHED — skipping]
STAGE 3: Voiceover Generation
...
```

### Auto-versioning

Running the same script again won't overwrite previous output:

- First run: `deep_thoughts_01.mp4`
- Second run: `deep_thoughts_01_v2.mp4`
- Third run: `deep_thoughts_01_v3.mp4`

## Release Calendar

A built-in calendar system for scheduling and tracking video releases across multiple channels.

### CLI

```bash
# View current schedule
python -m src.publishing.calendar_manager status

# Add a channel (single time)
python -m src.publishing.calendar_manager add-channel \
  --id business --name "Business Channel" --days tue,thu --time 11:00

# Add a channel (multiple daily times)
python -m src.publishing.calendar_manager add-channel \
  --id deep_thoughts --name "Deep Thoughts" --days mon,tue,wed,thu,fri,sat,sun --time "12:00,20:00"

# Generate 4 weeks of placeholder slots
python -m src.publishing.calendar_manager generate --weeks 4

# Upload any videos due in the next 48 hours
python -m src.publishing.calendar_manager publish-due
```

### Web UI

```bash
python -m src.web.calendar_server
```

Opens the web interface at `http://localhost:5555` with two views:

**Pipeline** (`/`) — create videos from the browser:
- Select a channel, and its default settings auto-populate (vertical, captions, quality, publish, tags, etc.)
- Drag-and-drop a `.txt` script file or paste script text directly
- Enter a title and description
- Override any channel defaults with toggle switches
- Hit "Start Pipeline" — the job runs in the background with real-time stage progress and console log
- Job history shows recent runs and their status

**Calendar** (`/calendar`) — view and manage the release schedule:
- Per-channel tabs with slot counts and schedule info
- Monthly calendar grid showing all scheduled slots
- Click any slot to view/edit details, assign videos, or delete
- Modals for adding channels and generating placeholder slots

## Output

Each script gets its own folder in the workspace:

```
channels/
└── deep_thoughts/
    ├── default_settings.json       # Channel pipeline + publishing defaults
    ├── content_prompt.md           # LLM prompt for scripts/titles/descriptions
    ├── youtube_token.json          # Per-channel OAuth token (auto-created)
    └── workspace/
        └── short_01/
            ├── clips/              # Downloaded stock footage
            ├── audio/
            │   └── narration.mp3   # Generated voiceover (mastered)
            ├── overlays/           # Text overlay PNGs (when --overlays used)
            ├── thumbnails/         # Thumbnail images (future use)
            ├── credits/
            │   └── credits.txt     # Pexels videographer attribution
            ├── output/
            │   └── short_01.mp4    # Rendered video
            ├── 1_segments.json     # AI script analysis
            ├── 2_segments_with_footage.json
            ├── 3_alignment.json    # Character-level timing
            ├── 3_segments_with_timing.json
            ├── 4_edl.json          # Edit Decision List
            └── captions.srt        # Subtitle file (when --captions used)
```

Without `--channel`, the legacy `workspace/` directory is used instead.

## Project Structure

```
src/
├── main.py                        # Orchestrator — runs the full pipeline
├── config.py                      # Settings, API keys, project directories
├── __init__.py
├── __main__.py                    # Entry point for python -m src
│
├── pipeline/                      # Core video production stages
│   ├── script_analyzer.py         # Stage 1: Script → visual segments
│   ├── footage_finder.py          # Stage 2: Pexels search → download clips
│   ├── voiceover.py               # Stage 3: ElevenLabs TTS + timestamps
│   ├── text_overlay.py            # Stage 1.5: Styled text overlay PNGs
│   ├── captions.py                # Stage 3.5: SRT caption generation
│   ├── timeline_builder.py        # Stage 4: AI → Edit Decision List
│   └── video_assembler.py         # Stage 5: FFmpeg → final MP4
│
├── publishing/                    # YouTube + scheduling
│   ├── publisher.py               # YouTube Data API v3 upload
│   └── calendar_manager.py        # Release calendar CLI + logic
│
├── web/                           # Web interfaces
│   ├── calendar_server.py         # Threaded HTTP server (pipeline + calendar)
│   ├── pipeline_runner.py         # Background job runner for web UI
│   └── static/
│       ├── pipeline.html          # Pipeline UI (create videos)
│       └── calendar.html          # Calendar UI (manage schedule)
│
└── utils/                         # Shared helpers
    ├── llm.py                     # OpenAI-compatible chat helper
    └── rate_limiter.py            # Sliding-window API rate limiter

scripts/                           # Your .txt video scripts
channels/                          # Per-channel workspaces, settings, prompts, tokens (gitignored)
channels_example/                  # Template for setting up new channels (includes content_prompt.md template)
docs/                              # Development roadmap and planning notes
```

## Long-Form Content

The pipeline is designed for long-form videos (1+ hours). Key features that enable this:

- **Chunked script analysis** — Large scripts are split into ~5K-char chunks for LLM processing
- **ElevenLabs Request Stitching** — Voice consistency across TTS chunks
- **Pexels rate limiter** — Automatic pause/resume at 200 req/hr limit
- **Batched EDL generation** — Timeline built in 25-segment batches
- **FFmpeg-direct rendering** — Memory-efficient, processes one clip at a time
- **Checkpoint/resume** — No wasted API calls on re-runs

**Estimated pipeline time for a 1-hour video:** ~4-5 hours (mostly Pexels API rate limiting).

## Cross-Platform

The codebase uses `pathlib.Path` and `subprocess` with `ffmpeg`/`ffprobe`, so it runs on both Windows and macOS/Linux.

- **FFmpeg** — On Mac, install with `brew install ffmpeg` and leave `FFMPEG_PATH` unset. The `FFMPEG_PATH` env var is only needed if auto-detection hangs on Windows.
- **Timezone data** — The `tzdata` package is included in requirements for Windows support (Linux/Mac have system tzdata).
- **Checkpoint portability** — Checkpoint JSON files store absolute paths. If you copy the workspace between machines, the pipeline resolves paths by filename. Use `--fresh` for a clean start if needed.
