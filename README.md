# script2vid

Turn a written script into a fully assembled video with AI-selected stock footage and narrated voiceover — no manual editing required.

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
- No video editing software — MoviePy + FFmpeg render the final video

## How It Works

1. **Script Analysis** — An AI breaks your script into visual segments with search keywords
2. **Footage Retrieval** — Searches Pexels for stock footage matching each segment
3. **Voiceover Generation** — ElevenLabs generates narration audio with character-level timestamps
4. **Timeline Assembly** — An AI agent creates an Edit Decision List (EDL) mapping clips to the audio timeline, using precise timestamp data so footage stays in sync with narration
5. **Video Rendering** — MoviePy executes the EDL and renders the final MP4 (clip audio is muted — only the narrator is heard)

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
```

## Usage

Place your script in the `scripts/` folder as a `.txt` file, then run:

```bash
python -m src scripts/deep_thoughts_01.txt
```

Or pass a script directly:

```bash
python -m src --script "Your video script text here."
```

That's it. The pipeline runs automatically and outputs a finished video.

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
    │   └── narration.mp3             # Generated voiceover
    ├── output/
    │   ├── deep_thoughts_01.mp4      # First run
    │   └── deep_thoughts_01_v2.mp4   # Second run (auto-versioned)
    ├── 1_segments.json               # Script segments from AI analysis
    ├── 2_segments_with_footage.json  # Segments with matched footage
    ├── 3_alignment.json              # Character-level timing from ElevenLabs
    ├── 3_segments_with_timing.json   # Segments with audio time ranges
    └── 4_edl.json                    # The Edit Decision List
```

The JSON files are saved for debugging — you can inspect them to see exactly what the AI decided at each stage.

## Project Structure

```
scripts/                 # Put your .txt scripts here
src/
├── main.py              # Orchestrator — runs the full pipeline
├── config.py            # Settings, API keys, per-script project folders
├── llm.py               # Shared LLM helper (OpenAI-compatible)
├── script_analyzer.py   # Stage 1: Script → visual segments
├── footage_finder.py    # Stage 2: Pexels search → download clips
├── voiceover.py         # Stage 3: ElevenLabs TTS + timestamps
├── timeline_builder.py  # Stage 4a: AI → Edit Decision List
└── video_assembler.py   # Stage 4b+5: MoviePy → final MP4
```
