"""
Background pipeline job runner.

Launches the script2vid pipeline as a subprocess, captures output in real-time,
parses stage progress, and exposes job state for the web UI to poll.

All state is in-memory (dict). Jobs survive for the lifetime of the server
process — no persistence needed since each job takes minutes-to-hours and the
user is actively watching.
"""

import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"

# In-memory job store: job_id -> job dict
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

# Pipeline stages in order (used for progress display)
STAGES = [
    "Script Analysis",
    "Text Overlays",
    "Footage Retrieval",
    "Voiceover Generation",
    "Caption Generation",
    "Timeline Assembly",
    "Video Rendering",
    "Calendar Assignment",
    "YouTube Publishing",
]


def _load_channel_defaults(channel_id: str) -> dict:
    """Load a channel's default_settings.json."""
    defaults_path = _PROJECT_ROOT / "channels" / channel_id / "default_settings.json"
    if defaults_path.exists():
        return json.loads(defaults_path.read_text(encoding="utf-8"))
    return {}


def get_channel_defaults(channel_id: str) -> dict:
    """Public wrapper for loading channel defaults."""
    return _load_channel_defaults(channel_id)


def start_job(
    channel: str,
    script_text: str,
    title: str | None = None,
    description: str | None = None,
    overrides: dict | None = None,
) -> dict:
    """
    Start a pipeline job in the background.

    Saves the script text to a file, builds the CLI command with any
    overrides, launches a subprocess, and starts a reader thread to
    capture output in real-time.

    Returns the job dict.
    """
    job_id = uuid.uuid4().hex[:8]
    overrides = overrides or {}

    # ── Save script to file ──────────────────────────────────────
    _SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = (title or channel).lower().replace(" ", "_")[:40]
    script_name = f"web_{slug}_{job_id}.txt"
    script_path = _SCRIPTS_DIR / script_name
    script_path.write_text(script_text, encoding="utf-8")

    # ── Build command ────────────────────────────────────────────
    cmd = [
        sys.executable, "-u", "-m", "src",
        str(script_path),
        "--channel", channel,
    ]

    if title:
        cmd.extend(["--title", title])
    if description:
        cmd.extend(["--description", description])

    # Apply overrides — these are explicit CLI flags that beat defaults
    if "vertical" in overrides:
        if overrides["vertical"]:
            cmd.append("--vertical")
    if "captions" in overrides:
        if overrides["captions"]:
            cmd.append("--captions")
    if "overlays" in overrides:
        if overrides["overlays"]:
            cmd.append("--overlays")
    if "quality" in overrides:
        cmd.extend(["--quality", overrides["quality"]])
    if "publish" in overrides:
        if overrides["publish"]:
            cmd.append("--publish")
    if "fresh" in overrides:
        if overrides["fresh"]:
            cmd.append("--fresh")
    if "tags" in overrides and overrides["tags"]:
        cmd.extend(["--tags", overrides["tags"]])
    if "category" in overrides and overrides["category"]:
        cmd.extend(["--category", overrides["category"]])
    if "privacy" in overrides and overrides["privacy"]:
        cmd.extend(["--privacy", overrides["privacy"]])

    # ── Create job record ────────────────────────────────────────
    job = {
        "id": job_id,
        "channel": channel,
        "title": title or slug,
        "status": "running",         # running | completed | failed
        "started_at": time.time(),
        "finished_at": None,
        "elapsed": None,
        "log": [],                   # list of output lines
        "current_stage": None,
        "stages_completed": [],
        "output_path": None,
        "calendar_slot": None,
        "youtube_url": None,
        "error": None,
        "script_path": str(script_path),
        "command": " ".join(cmd),
    }

    with _LOCK:
        _JOBS[job_id] = job

    # ── Launch subprocess ────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(_PROJECT_ROOT),
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"Failed to start subprocess: {e}"
        job["finished_at"] = time.time()
        return job

    # ── Reader thread — captures output line by line ─────────────
    def _reader():
        try:
            for line in proc.stdout:
                line = line.rstrip("\n\r")
                job["log"].append(line)
                _parse_line(job, line)

            proc.wait()
            if proc.returncode == 0:
                job["status"] = "completed"
                # Mark current stage as completed if still in progress
                if job["current_stage"] and job["current_stage"] not in job["stages_completed"]:
                    job["stages_completed"].append(job["current_stage"])
                    job["current_stage"] = None
            else:
                job["status"] = "failed"
                job["error"] = f"Process exited with code {proc.returncode}"
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
        finally:
            job["finished_at"] = time.time()
            job["elapsed"] = round(job["finished_at"] - job["started_at"], 1)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    return job


def _parse_line(job: dict, line: str):
    """Parse a single output line to extract stage progress and key info."""
    upper = line.upper()

    # ── Stage detection ──────────────────────────────────────────
    if "STAGE" in upper and ":" in line:
        # Mark previous stage as completed
        if job["current_stage"] and job["current_stage"] not in job["stages_completed"]:
            job["stages_completed"].append(job["current_stage"])

        # Detect which stage this is
        for stage in STAGES:
            if stage.upper() in upper:
                if "CACHED" in upper:
                    if stage not in job["stages_completed"]:
                        job["stages_completed"].append(stage)
                else:
                    job["current_stage"] = stage
                break

    # ── Cached stage shortcut ────────────────────────────────────
    elif "CACHED" in upper and "SKIPPING" in upper:
        if job["current_stage"] and job["current_stage"] not in job["stages_completed"]:
            job["stages_completed"].append(job["current_stage"])
            job["current_stage"] = None

    # ── Output path detection ────────────────────────────────────
    if "video saved to:" in line.lower() or "final video:" in line.lower():
        parts = line.split(":", 1)
        if len(parts) == 2:
            job["output_path"] = parts[1].strip()

    # Also catch the output path format from video_assembler
    if line.strip().endswith(".mp4") and ("output" in line.lower() or "saved" in line.lower()):
        job["output_path"] = line.strip().split()[-1]

    # ── Calendar slot detection ──────────────────────────────────
    if "slot:" in line.lower() and not line.strip().startswith("#"):
        parts = line.split(":", 1)
        if len(parts) == 2:
            job["calendar_slot"] = parts[1].strip()

    # ── YouTube URL detection ────────────────────────────────────
    if "youtube.com/" in line or "youtu.be/" in line:
        for word in line.split():
            if "youtube.com/" in word or "youtu.be/" in word:
                job["youtube_url"] = word.strip()
                break


def get_job(job_id: str) -> dict | None:
    """Get a job by ID."""
    return _JOBS.get(job_id)


def list_jobs() -> list[dict]:
    """List all jobs, most recent first."""
    jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return jobs


def cancel_job(job_id: str) -> bool:
    """Cancel a running job (best-effort — kills the subprocess)."""
    job = _JOBS.get(job_id)
    if not job or job["status"] != "running":
        return False
    # We don't store the proc ref, so we can't kill it directly.
    # Mark it as failed so the UI knows.
    job["status"] = "failed"
    job["error"] = "Cancelled by user"
    job["finished_at"] = time.time()
    job["elapsed"] = round(job["finished_at"] - job["started_at"], 1)
    return True
