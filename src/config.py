"""
Central configuration — loads from .env file and exposes settings.
"""

import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

# ---------------------------------------------------------------------------
# FFmpeg — must be set BEFORE moviepy / imageio_ffmpeg are imported.
# MoviePy 2.x on Windows can deadlock during auto-detection; bypassing it
# with an explicit path prevents the hang.
# ---------------------------------------------------------------------------
if not os.environ.get("IMAGEIO_FFMPEG_EXE"):
    _ffmpeg = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
    if _ffmpeg:
        os.environ["IMAGEIO_FFMPEG_EXE"] = _ffmpeg

# ---------------------------------------------------------------------------
# LLM (any OpenAI-compatible API: Kimi 2.5, OpenAI, etc.)
# ---------------------------------------------------------------------------
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "kimi-k2.5")

# ---------------------------------------------------------------------------
# Pexels
# ---------------------------------------------------------------------------
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PEXELS_BASE_URL = "https://api.pexels.com/videos"

# ---------------------------------------------------------------------------
# ElevenLabs
# ---------------------------------------------------------------------------
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"

# ---------------------------------------------------------------------------
# Output settings
# ---------------------------------------------------------------------------
_resolution = os.getenv("OUTPUT_RESOLUTION", "1920x1080").split("x")
OUTPUT_WIDTH = int(_resolution[0])
OUTPUT_HEIGHT = int(_resolution[1])
OUTPUT_FPS = int(os.getenv("OUTPUT_FPS", "30"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORK_DIR = _project_root / "workspace"
CHANNELS_DIR = _project_root / "channels"


def create_project_dirs(project_name: str, channel: str | None = None) -> dict:
    """
    Create a per-script project folder.

    When *channel* is provided the workspace is nested under that channel's
    directory so every channel is fully self-contained::

        channels/
        └── deep_thoughts/
            └── workspace/
                └── short_01/
                    ├── audio/
                    ├── clips/
                    ├── credits/
                    ├── overlays/
                    └── output/

    Without a channel the legacy layout is used::

        workspace/
        └── deep_thoughts_01/
            └── ...

    Returns a dict of paths.
    """
    if channel:
        root = CHANNELS_DIR / channel / "workspace"
    else:
        root = WORK_DIR

    root.mkdir(parents=True, exist_ok=True)

    project_dir = root / project_name
    clips_dir = project_dir / "clips"
    audio_dir = project_dir / "audio"
    output_dir = project_dir / "output"
    credits_dir = project_dir / "credits"
    overlays_dir = project_dir / "overlays"
    thumbnails_dir = project_dir / "thumbnails"

    for d in (project_dir, clips_dir, audio_dir, output_dir, credits_dir, overlays_dir, thumbnails_dir):
        d.mkdir(parents=True, exist_ok=True)

    return {
        "project_dir": project_dir,
        "clips_dir": clips_dir,
        "audio_dir": audio_dir,
        "output_dir": output_dir,
        "credits_dir": credits_dir,
        "overlays_dir": overlays_dir,
        "thumbnails_dir": thumbnails_dir,
    }
