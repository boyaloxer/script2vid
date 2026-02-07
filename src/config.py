"""
Central configuration — loads from .env file and exposes settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

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
WORK_DIR.mkdir(parents=True, exist_ok=True)


def create_project_dirs(project_name: str) -> dict:
    """
    Create a per-script project folder inside workspace.
    Returns a dict of paths: project_dir, clips_dir, audio_dir, output_dir.

    Structure:
        workspace/
        └── deep_thoughts_01/
            ├── clips/
            ├── audio/
            └── output/
    """
    project_dir = WORK_DIR / project_name
    clips_dir = project_dir / "clips"
    audio_dir = project_dir / "audio"
    output_dir = project_dir / "output"

    for d in (project_dir, clips_dir, audio_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)

    return {
        "project_dir": project_dir,
        "clips_dir": clips_dir,
        "audio_dir": audio_dir,
        "output_dir": output_dir,
    }
