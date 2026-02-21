# script2vid

An autonomous AI agent that manages YouTube channels end-to-end — from trend research and script generation to video production, publishing, and performance optimization. No manual editing required.

The agent runs continuously: it scouts trending topics in your niche, writes scripts, produces videos with AI-selected stock footage and narrated voiceover, publishes them, monitors performance metrics, learns from audience feedback, and iterates to improve content quality over time.

## Features

### Autonomous Agent
- **Observe → Think → Act → Reflect** loop powered by an LLM (Kimi K2.5 / any OpenAI-compatible model)
- Continuous operation with intelligent idle periods (waits for metrics to accumulate, then resumes)
- Trend scouting — discovers rising topics in your channel's niche
- Audience intelligence — analyzes comments and engagement patterns
- Content strategy generation — plans video topics based on what's working
- Multi-perspective critic — three parallel AI reviewers (Devil's Advocate, Viewer Simulator, Style Auditor) evaluate every script before production
- A/B experiment engine — tests title formats, opening hooks, and other variables with statistical rigor
- Post-publish optimization — monitors recent uploads and adjusts metadata
- Persistent memory — beliefs, episode history, and strategies survive across sessions
- Training dataset builder — captures agent decisions and outcomes for future model fine-tuning

### Video Production Pipeline
- **Script Analysis** — AI breaks scripts into visual segments with search keywords
- **Footage Retrieval** — Pexels stock footage, auto-selected and downloaded
- **Voiceover** — ElevenLabs TTS with character-level timestamps
- **Captions** — SRT subtitles synced to narration
- **Text Overlays** — Styled PNGs for quotes, statistics, citations
- **Timeline Assembly** — AI-generated Edit Decision List
- **Video Rendering** — FFmpeg processes and assembles the final MP4
- **YouTube Publishing** — Uploads, scheduling, metadata, and thumbnail support

### Dashboard
- Real-time 3D visualization of the agent's think-act cycle
- Live activity feed showing every decision the agent makes
- Channel metrics, strategy, memory, and experiment status at a glance
- **Chat with the agent** — ask questions, get status updates, discuss strategy
- **Command input** — queue instructions for the agent (e.g., `/check metrics`, `/make a video about...`)

### Multi-Channel Support
- Manage multiple YouTube channels from a single installation
- Per-channel settings, OAuth tokens, content prompts, and strategies
- Each channel has its own workspace, calendar, and publishing schedule

## What You Need

| What | Where to get it | Cost |
|---|---|---|
| **LLM API key** | [Moonshot / Kimi K2.5](https://platform.moonshot.ai/), [OpenAI](https://platform.openai.com/), or any OpenAI-compatible provider | Varies |
| **Pexels API key** | [pexels.com/api](https://www.pexels.com/api/) | Free |
| **ElevenLabs API key** | [elevenlabs.io](https://elevenlabs.io) | Free tier available |
| **FFmpeg** | [ffmpeg.org](https://ffmpeg.org/download.html) | Free |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

FFmpeg is also required:
- **Windows:** `winget install FFmpeg`
- **Mac:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`

### 2. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` with your keys (see `.env.example` for all options including YouTube Analytics).

### 3. Set up a channel

```bash
python -m src.publishing.calendar_manager add-channel \
  --id my_channel \
  --name "My Channel" \
  --days mon,wed,fri \
  --time "12:00" \
  --timezone America/New_York

python -m src.publishing.calendar_manager generate --weeks 4
```

Create your channel's content prompt at `channels/my_channel/content_prompt.md` — this defines the channel's voice, style, and content format. See `channels_example/` for a template.

### 4. (Optional) YouTube API setup

To enable publishing, metrics, and full autonomous operation:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable **YouTube Data API v3** and **YouTube Analytics API**
3. Create **OAuth 2.0 Desktop App** credentials
4. Download as `client_secrets.json` in the project root
5. First publish opens a browser for authorization — select the correct YouTube channel

Per-channel tokens are saved automatically at `channels/<id>/youtube_token.json`.

## Usage

### Run the Autonomous Agent

```bash
# Run the agent for a single channel (recommended)
python -Bu -m src.agent.runner --channel my_channel

# Run for all configured channels
python -Bu -m src.agent.runner --all

# Single session then exit (no continuous loop)
python -Bu -m src.agent.runner --channel my_channel --once

# Dry run — generate scripts but don't produce or upload
python -Bu -m src.agent.runner --channel my_channel --dry-run
```

The agent will:
1. Build a picture of the world (channel state, metrics, pending work)
2. Decide what to do next (scout trends, generate content, check metrics, etc.)
3. Execute the action (write a script, produce a video, publish, analyze)
4. Reflect on the outcome and update its memory
5. Repeat — or idle and check back later if waiting for metrics

### Launch the Dashboard

```bash
python -B -m src.web.calendar_server --port 5560
```

Opens at `http://localhost:5560/dashboard`. Run this alongside the agent to watch it work in real-time.

### Chat with the Agent

In the dashboard, use the input bar at the bottom:
- **Type normally** to chat — ask questions, get status updates, discuss strategy
- **Start with `/`** to queue a command — e.g., `/check metrics`, `/scout trends`, `/make a video about quantum physics`

Or use the terminal chat:

```bash
python chat.py --channel my_channel
```

### Run the Pipeline Directly

You can also use the pipeline without the agent for one-off video production:

```bash
# Basic usage
python -u -m src scripts/my_video.txt

# With channel defaults, captions, and auto-publish
python -u -m src scripts/my_video.txt --channel my_channel --captions

# Vertical short-form
python -u -m src scripts/my_short.txt --vertical --captions

# Draft quality for quick preview
python -u -m src scripts/my_video.txt --quality draft
```

| Flag | Description |
|---|---|
| `--channel ID` | Use channel settings, workspace, calendar, and auto-publish |
| `--quality draft/final` | Rendering quality (ultrafast vs medium FFmpeg preset) |
| `--captions` | Burn subtitles into the video |
| `--vertical` | 9:16 format for Shorts/Reels/TikTok |
| `--overlays` | Text overlays for quotes and citations |
| `--publish` | Upload to YouTube after rendering |
| `--schedule ISO` | Schedule YouTube publish time |
| `--fresh` | Ignore checkpoints, re-run from scratch |
| `--dry-run` | Generate scripts only |

### Checkpoint / Resume

Every pipeline stage saves checkpoints. If interrupted, re-run the same command — completed stages are skipped automatically.

## Project Structure

```
src/
├── agent/                        # Autonomous agent system
│   ├── runner.py                 # Main observe → think → act → reflect loop
│   ├── brain.py                  # LLM-powered decision engine
│   ├── observer.py               # World state builder
│   ├── memory.py                 # Persistent beliefs, episodes, scratchpad
│   ├── script_generator.py       # LLM script generation from content prompts
│   ├── critic.py                 # Multi-perspective script review (3 reviewers)
│   ├── strategist.py             # Content strategy generation
│   ├── trend_scout.py            # Niche trend discovery
│   ├── audience.py               # Comment and engagement analysis
│   ├── analytics.py              # YouTube metrics collection
│   ├── optimizer.py              # Post-publish video optimization
│   ├── scheduler.py              # Optimal posting time analysis
│   ├── community.py              # Comment engagement
│   ├── experiment_engine.py      # A/B testing framework
│   ├── dataset_builder.py        # Training data capture
│   ├── agent_chat.py             # Real-time chat with the agent
│   ├── command_queue.py          # User command queue
│   ├── activity_feed.py          # Real-time event stream
│   └── journal.py                # Session logging
│
├── pipeline/                     # Video production stages
│   ├── script_analyzer.py        # Script → visual segments
│   ├── footage_finder.py         # Pexels search → download clips
│   ├── voiceover.py              # ElevenLabs TTS + timestamps
│   ├── text_overlay.py           # Styled text overlay PNGs
│   ├── captions.py               # SRT caption generation
│   ├── timeline_builder.py       # AI → Edit Decision List
│   └── video_assembler.py        # FFmpeg → final MP4
│
├── publishing/                   # YouTube + scheduling
│   ├── publisher.py              # YouTube Data API v3 upload
│   └── calendar_manager.py       # Release calendar management
│
├── web/                          # Web interfaces
│   ├── calendar_server.py        # HTTP server (API + static)
│   ├── dashboard_api.py          # Dashboard data endpoints
│   ├── pipeline_runner.py        # Background job runner
│   └── static/
│       ├── dashboard.html        # Agent dashboard (3D viz, live feed, chat)
│       ├── pipeline.html         # Pipeline UI (manual video creation)
│       └── calendar.html         # Calendar UI (schedule management)
│
├── utils/                        # Shared helpers
│   ├── llm.py                    # OpenAI-compatible chat helper
│   ├── quota_tracker.py          # API usage tracking
│   ├── rate_limiter.py           # Sliding-window rate limiter
│   └── retry.py                  # Exponential backoff retry
│
├── config.py                     # Settings, API keys, directories
├── main.py                       # Pipeline orchestrator
└── __main__.py                   # Entry point (python -m src)

channels_example/                 # Channel setup template
chat.py                           # Terminal chat with the agent
```

## How It Works

### The Agent Loop

```
┌─────────────────────────────────────────────────┐
│                  OBSERVE                         │
│  Build world state: channel metrics, calendar,   │
│  pending work, API quotas, user commands         │
├─────────────────────────────────────────────────┤
│                   THINK                          │
│  LLM analyzes state and picks the best action:  │
│  generate content, scout trends, check metrics,  │
│  run experiments, optimize, or idle              │
├─────────────────────────────────────────────────┤
│                    ACT                           │
│  Execute: write script → critic review →         │
│  produce video → publish to YouTube              │
├─────────────────────────────────────────────────┤
│                  REFLECT                         │
│  Update memory, log outcomes, adjust strategy    │
│  for next cycle                                  │
└─────────────────────────────────────────────────┘
          ↓ idle 30m then repeat ↑
```

### The Critic

Every script passes through three independent AI reviewers before production:

- **Devil's Advocate** — actively tries to find reasons NOT to watch. Catches repetitive patterns, weak hooks, and derivative content
- **Viewer Simulator** — rates tap-through likelihood, watch-through probability, and emotional impact
- **Style Auditor** — checks compliance with the channel's voice, tone, and format rules

Fatal issues trigger automatic regeneration. The agent keeps iterating until the script passes.

### Experiments

The agent runs controlled A/B tests to optimize content:
- Tests different title formats, opening hooks, descriptions, and more
- Assigns videos to control/variant arms automatically
- Collects performance data and determines statistical significance
- Updates strategy based on what wins

## Long-Form Support

The pipeline handles videos up to 1+ hours through chunked processing at every stage — LLM analysis, TTS generation, footage retrieval, and timeline assembly all scale gracefully.

## Cross-Platform

Runs on Windows, macOS, and Linux. Uses `pathlib.Path` throughout and auto-detects FFmpeg.

## License

MIT
