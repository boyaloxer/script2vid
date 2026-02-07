"""
script2vid — Main Orchestrator

Runs the full pipeline:
    Script → Analysis → Footage + Voiceover (parallel-ready) → Timeline → Video

Usage:
    python -m src.main "path/to/script.txt"
    python -m src.main --script "Your script text here directly"
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from src.config import create_project_dirs
from src.script_analyzer import analyze_script
from src.footage_finder import find_footage_for_segments
from src.voiceover import generate_voiceover, map_segments_to_time_ranges
from src.timeline_builder import build_timeline
from src.video_assembler import assemble_video


def _next_version_name(output_dir: Path, project_name: str) -> str:
    """
    Auto-version output files so re-runs don't overwrite previous videos.
    First run:  deep_thoughts_01.mp4
    Second run: deep_thoughts_01_v2.mp4
    Third run:  deep_thoughts_01_v3.mp4
    """
    base = f"{project_name}.mp4"
    if not (output_dir / base).exists():
        return base

    version = 2
    while True:
        versioned = f"{project_name}_v{version}.mp4"
        if not (output_dir / versioned).exists():
            return versioned
        version += 1


def _derive_project_name(script_file: str | None, script_text: str) -> str:
    """
    Derive a clean project name from the script filename or text.
    Used to create the per-script workspace folder and output video name.
    """
    if script_file:
        # Use the filename without extension: "deep_thoughts_01.txt" → "deep_thoughts_01"
        return Path(script_file).stem

    # No file — generate a slug from the first few words of the script
    words = re.sub(r"[^\w\s]", "", script_text).split()[:5]
    slug = "_".join(words).lower()
    return slug or "untitled"


def run_pipeline(script_text: str, project_name: str) -> Path:
    """
    Execute the full script-to-video pipeline.

    Args:
        script_text: The raw video script.
        project_name: Name for the project folder and output video.

    Returns:
        Path to the rendered output video.
    """
    total_start = time.time()

    # Create per-script workspace folders
    paths = create_project_dirs(project_name)
    project_dir = paths["project_dir"]
    clips_dir = paths["clips_dir"]
    audio_dir = paths["audio_dir"]
    output_dir = paths["output_dir"]

    print(f"\nProject: {project_name}")
    print(f"Workspace: {project_dir}")

    def _save_json(data, name: str) -> Path:
        """Save intermediate data to project folder for debugging."""
        path = project_dir / name
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return path

    # ──────────────────────────────────────────────
    # Stage 1: Script Analysis
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STAGE 1: Script Analysis")
    print("=" * 60)
    segments = analyze_script(script_text)
    _save_json(segments, "1_segments.json")

    # ──────────────────────────────────────────────
    # Stage 2: Footage Retrieval
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STAGE 2: Footage Retrieval")
    print("=" * 60)
    segments = find_footage_for_segments(segments, clips_dir)
    _save_json(segments, "2_segments_with_footage.json")

    # ──────────────────────────────────────────────
    # Stage 3: Voiceover Generation + Timestamps
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STAGE 3: Voiceover Generation")
    print("=" * 60)

    # Reconstruct the full script from segments to ensure alignment
    full_script = " ".join(seg["text"] for seg in segments)
    audio_path, alignment = generate_voiceover(full_script, audio_dir)
    _save_json(alignment, "3_alignment.json")

    # Map timing back onto segments (pass audio_path so we can get total duration)
    segments = map_segments_to_time_ranges(segments, alignment, audio_path)
    _save_json(segments, "3_segments_with_timing.json")

    # ──────────────────────────────────────────────
    # Stage 4a: Timeline Assembly (AI → EDL)
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STAGE 4: Timeline Assembly")
    print("=" * 60)
    edl = build_timeline(segments)
    _save_json(edl, "4_edl.json")

    # ──────────────────────────────────────────────
    # Stage 4b + 5: Video Assembly & Rendering
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STAGE 5: Video Rendering")
    print("=" * 60)
    output_name = _next_version_name(output_dir, project_name)
    output_path = assemble_video(edl, audio_path, output_dir, output_name)

    elapsed = time.time() - total_start
    print("\n" + "=" * 60)
    print(f"PIPELINE COMPLETE — {elapsed:.1f}s total")
    print(f"Output: {output_path}")
    print("=" * 60)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="script2vid: Turn a script into a video with AI.",
    )
    parser.add_argument(
        "script_file",
        nargs="?",
        help="Path to a .txt file containing the video script.",
    )
    parser.add_argument(
        "--script",
        type=str,
        help="Pass the script text directly as a string.",
    )
    args = parser.parse_args()

    if args.script:
        script_text = args.script
        script_file = None
    elif args.script_file:
        path = Path(args.script_file)
        if not path.exists():
            print(f"Error: File not found: {path}")
            sys.exit(1)
        script_text = path.read_text(encoding="utf-8")
        script_file = args.script_file
    else:
        print("Error: Provide a script file or use --script 'text'")
        parser.print_help()
        sys.exit(1)

    if not script_text.strip():
        print("Error: Script is empty.")
        sys.exit(1)

    project_name = _derive_project_name(script_file, script_text)
    run_pipeline(script_text, project_name)


if __name__ == "__main__":
    main()
