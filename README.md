# script2vid

Turn a written script into a fully assembled video with AI-selected stock footage and narrated voiceover — no manual editing required. Supports both short-form and long-form content (tested up to 1+ hour videos).

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

1. **Script Analysis** — An AI breaks your script into visual segments with search keywords (chunked for large scripts)
2. **Footage Retrieval** — Searches Pexels for stock footage matching each segment (rate-limited, with automatic pause/resume)
3. **Voiceover Generation** — ElevenLabs generates narration audio with character-level timestamps (chunked with Request Stitching for long scripts, then mastered via dynaudnorm + loudnorm for consistent volume)
4. **Timeline Assembly** — An AI agent creates an Edit Decision List (EDL) mapping clips to the audio timeline, using slot-based timing so footage stays in sync with narration (batched for large segment counts)
5. **Video Rendering** — FFmpeg processes each clip individually, concatenates them, and overlays the narration audio (clip audio is muted — only the narrator is heard)

All intermediate data is saved as checkpoints. If the pipeline is interrupted, re-running picks up where it left off.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

This installs: `moviepy` (used for audio duration probing), `requests` (API calls), `python-dotenv` (config loading).

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

### 3. (Optional) Adjust output settings

In `.env` you can also set:

```env
OUTPUT_RESOLUTION=1920x1080   # default: 1080p
OUTPUT_FPS=30                 # default: 30fps

# Only needed if FFmpeg auto-detection hangs on Windows. Leave blank to auto-detect.
# FFMPEG_PATH=C:\\path\\to\\ffmpeg.exe
```

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

Examples:

```bash
# Draft quality for quick preview
python -u -m src scripts/deep_thoughts_01.txt --quality draft

# Final quality for YouTube upload
python -u -m src scripts/deep_thoughts_01.txt --quality final

# Force re-run everything (ignore cached stages)
python -u -m src scripts/deep_thoughts_01.txt --fresh
```

### Checkpoint / Resume

Each pipeline stage saves its output as a JSON file. If the pipeline is interrupted (API error, timeout, crash), simply re-run the same command. Completed stages are detected and skipped automatically:

```
STAGE 1: Script Analysis [CACHED — skipping]
STAGE 2: Footage Retrieval [CACHED — skipping]
STAGE 3: Voiceover Generation
...
```

This is especially valuable for long-form content where Stages 1-2 can take hours due to API rate limits.

### Re-running the same script

Running the same script again won't overwrite previous output. Videos are auto-versioned:

- First run: `deep_thoughts_01.mp4`
- Second run: `deep_thoughts_01_v2.mp4`
- Third run: `deep_thoughts_01_v3.mp4`

This lets you compare results and upload the best one.

## Output

Each script gets its own folder in `workspace/`, named after the script file:

```
workspace/
└── deep_thoughts_01/
    ├── clips/                        # Downloaded stock footage
    ├── audio/
    │   └── narration.mp3             # Generated voiceover (mastered: dynaudnorm + loudnorm, stereo)
    ├── credits/
    │   └── credits.txt               # Pexels videographer attribution
    ├── output/
    │   ├── deep_thoughts_01.mp4      # First run
    │   └── deep_thoughts_01_v2.mp4   # Second run (auto-versioned)
    ├── 1_segments.json               # Script segments from AI analysis
    ├── 2_segments_with_footage.json  # Segments with matched footage
    ├── 3_alignment.json              # Character-level timing from ElevenLabs
    ├── 3_segments_with_timing.json   # Segments with audio time ranges
    └── 4_edl.json                    # The Edit Decision List
```

The JSON files are saved for debugging and checkpointing — you can inspect them to see exactly what the AI decided at each stage.

## Long-Form Content

The pipeline is designed for long-form videos (1+ hours). Key features that enable this:

- **Chunked script analysis** — Large scripts are split into ~5K-char chunks for LLM processing
- **ElevenLabs Request Stitching** — Voice consistency across TTS chunks
- **Pexels rate limiter** — Automatic pause/resume at 200 req/hr limit
- **Batched EDL generation** — Timeline built in 25-segment batches
- **FFmpeg-direct rendering** — Memory-efficient, processes one clip at a time
- **Checkpoint/resume** — No wasted API calls on re-runs

**Estimated pipeline time for a 1-hour video:** ~4-5 hours (mostly Pexels API rate limiting).

## Project Structure

```
scripts/                 # Put your .txt scripts here
src/
├── main.py              # Orchestrator — runs the full pipeline
├── config.py            # Settings, API keys, per-script project folders
├── llm.py               # Shared LLM helper (OpenAI-compatible)
├── rate_limiter.py      # Generic sliding-window rate limiter
├── script_analyzer.py   # Stage 1: Script → visual segments (chunked)
├── footage_finder.py    # Stage 2: Pexels search → download clips
├── voiceover.py         # Stage 3: ElevenLabs TTS + timestamps + audio mastering
├── timeline_builder.py  # Stage 4: AI → Edit Decision List (batched)
└── video_assembler.py   # Stage 5: FFmpeg-direct → final MP4
```
