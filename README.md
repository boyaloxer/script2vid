# script2vid

Turn a written script into a fully assembled video with AI-selected stock footage and narrated voiceover — no manual editing required.

## What You Provide

All you need is **3 API keys** and **a script**. Everything else is automated.

| What | Where to get it | Cost |
|---|---|---|
| **LLM API key** | Any OpenAI-compatible provider: [Moonshot / Kimi 2.5](https://platform.moonshot.cn/), [OpenAI](https://platform.openai.com/), etc. | Varies by provider |
| **Pexels API key** | [pexels.com](https://www.pexels.com/api/) — sign up and get a key | Free |
| **ElevenLabs API key** | [elevenlabs.io](https://elevenlabs.io) — sign up and get a key from your dashboard | Free tier available |
| **Your script** | A plain text file (`.txt`) or a string passed via command line | — |

**What you do NOT need to provide:**
- No video footage — searched and downloaded automatically from Pexels
- No audio files — generated automatically by ElevenLabs
- No editing decisions — the AI handles clip selection, trimming, and sequencing
- No video editing software — MoviePy + FFmpeg render the final video

## How It Works

1. **Script Analysis** — An AI breaks your script into visual segments with search keywords
2. **Footage Retrieval** — Searches Pexels for stock footage matching each segment
3. **Voiceover Generation** — ElevenLabs generates narration audio with character-level timestamps
4. **Timeline Assembly** — An AI agent creates an Edit Decision List (EDL) mapping clips to audio timing
5. **Video Rendering** — MoviePy executes the EDL and renders the final MP4

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

This installs: `moviepy` (video editing), `requests` (API calls), `python-dotenv` (config loading).

FFmpeg is also required (MoviePy uses it under the hood). Install it if you don't have it:
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
LLM_BASE_URL=https://api.moonshot.cn/v1    # change if using a different provider
LLM_MODEL=kimi-2.5                          # change to match your provider's model name

# Pexels
PEXELS_API_KEY=your_key_here

# ElevenLabs
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # optional — default voice is provided
```

### 3. (Optional) Adjust output settings

In `.env` you can also set:

```env
OUTPUT_RESOLUTION=1920x1080   # default: 1080p
OUTPUT_FPS=30                 # default: 30fps
```

## Usage

```bash
# From a script file
python -m src script.txt

# Or pass the script directly
python -m src --script "Artificial intelligence is transforming every industry. From healthcare to finance, machine learning models are automating tasks that once required human expertise."
```

That's it. The pipeline runs automatically and outputs a finished video.

## Output

The final video and all intermediate files are saved in `workspace/`:

```
workspace/
├── clips/                        # Downloaded stock footage
├── audio/
│   └── narration.mp3             # Generated voiceover
├── output/
│   └── final_video.mp4           # The finished video
├── 1_segments.json               # Script segments from AI analysis
├── 2_segments_with_footage.json  # Segments with matched footage
├── 3_alignment.json              # Character-level timing from ElevenLabs
├── 3_segments_with_timing.json   # Segments with audio time ranges
└── 4_edl.json                    # The Edit Decision List
```

The JSON files are saved for debugging — you can inspect them to see exactly what the AI decided at each stage.

## Project Structure

```
src/
├── main.py              # Orchestrator — runs the full pipeline
├── config.py            # Settings and API keys (from .env)
├── llm.py               # Shared LLM helper (OpenAI-compatible)
├── script_analyzer.py   # Stage 1: Script → visual segments
├── footage_finder.py    # Stage 2: Pexels search → download clips
├── voiceover.py         # Stage 3: ElevenLabs TTS + timestamps
├── timeline_builder.py  # Stage 4a: AI → Edit Decision List
└── video_assembler.py   # Stage 4b+5: MoviePy → final MP4
```
