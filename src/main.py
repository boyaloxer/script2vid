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
from pathlib import Path, PureWindowsPath

from src.config import create_project_dirs
from src.script_analyzer import analyze_script
from src.footage_finder import find_footage_for_segments
from src.voiceover import generate_voiceover, map_segments_to_time_ranges
from src.text_overlay import generate_overlays_for_segments
from src.captions import generate_srt
from src.timeline_builder import build_timeline
from src.video_assembler import assemble_video


def _generate_credits(segments: list[dict], credits_dir: Path) -> Path | None:
    """
    Generate a credits.txt file listing Pexels videographer attributions.
    Saved in the project's credits folder for easy copy-paste into descriptions.
    """
    credits_path = credits_dir / "credits.txt"

    lines = [
        "=== Video Credits ===",
        "Footage provided by Pexels (https://www.pexels.com)",
        "",
    ]

    has_credits = False
    for seg in segments:
        videographer = seg.get("pexels_videographer")
        video_url = seg.get("pexels_video_url")
        profile_url = seg.get("pexels_videographer_url")

        if not videographer or not video_url:
            continue

        has_credits = True
        seg_id = seg.get("segment_id", "?")
        lines.append(f"Segment {seg_id} — {videographer}")
        lines.append(f"  Video: {video_url}")
        if profile_url:
            lines.append(f"  Profile: {profile_url}")
        lines.append("")

    if not has_credits:
        return None

    credits_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[Credits] Saved attribution file to {credits_path}")
    return credits_path


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


def _load_json(path: Path) -> any:
    """Load a JSON file, returning None if it doesn't exist or is invalid."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def run_pipeline(
    script_text: str,
    project_name: str,
    fresh: bool = False,
    quality: str = "final",
    overlays: bool = False,
    captions: bool = False,
    vertical: bool = False,
) -> Path:
    """
    Execute the full script-to-video pipeline.

    Supports checkpoint/resume: if intermediate files from a previous run
    exist in the project folder, completed stages are skipped automatically.
    Use fresh=True to force re-running all stages from scratch.

    Args:
        script_text: The raw video script.
        project_name: Name for the project folder and output video.
        fresh: If True, ignore cached intermediate files and re-run everything.
        quality: "draft" for fast renders, "final" for production quality.
        overlays: If True, generate and composite text overlays for quotes,
            statistics, and source citations. Experimental — off by default.
        captions: If True, burn closed captions into the video using
            word-level timing from the TTS alignment data.
        vertical: If True, output is 1080x1920 (9:16) — adjusts caption
            positioning and line width for vertical viewing.

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
    credits_dir = paths["credits_dir"]
    overlays_dir = paths["overlays_dir"]

    print(f"\nProject: {project_name}")
    print(f"Workspace: {project_dir}")
    if fresh:
        print("Mode: FRESH (ignoring cached stages)")

    def _save_json(data, name: str) -> Path:
        """Save intermediate data to project folder for debugging."""
        path = project_dir / name
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return path

    def _has_checkpoint(name: str) -> bool:
        """Check if a checkpoint file exists (and we're not in fresh mode)."""
        if fresh:
            return False
        return (project_dir / name).exists()

    # ──────────────────────────────────────────────
    # Stage 1: Script Analysis
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    if _has_checkpoint("1_segments.json"):
        print("STAGE 1: Script Analysis [CACHED — skipping]")
        segments = _load_json(project_dir / "1_segments.json")
    else:
        print("STAGE 1: Script Analysis")
        print("=" * 60)
        segments = analyze_script(script_text)
        _save_json(segments, "1_segments.json")

    # ──────────────────────────────────────────────
    # Stage 1.5: Text Overlay Generation (opt-in)
    # ──────────────────────────────────────────────
    if overlays:
        has_overlays = any(
            seg.get("quote_type", "none") != "none" and seg.get("quote_text")
            for seg in segments
        )
        if has_overlays:
            print("\n" + "=" * 60)
            if all(seg.get("overlay_path") for seg in segments
                   if seg.get("quote_type", "none") != "none"):
                print("STAGE 1.5: Text Overlay Generation [CACHED — skipping]")
            else:
                print("STAGE 1.5: Text Overlay Generation")
                print("=" * 60)
                segments = generate_overlays_for_segments(segments, overlays_dir)
                # Re-save segments with overlay paths
                _save_json(segments, "1_segments.json")

    # ──────────────────────────────────────────────
    # Stage 2: Footage Retrieval
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    if _has_checkpoint("2_segments_with_footage.json"):
        print("STAGE 2: Footage Retrieval [CACHED — skipping]")
        segments = _load_json(project_dir / "2_segments_with_footage.json")
        # Resolve paths if workspace was copied from another OS (e.g. Windows → Mac).
        # Use PureWindowsPath to extract filename — it handles both / and \
        # separators, whereas PosixPath treats \ as a literal character.
        for seg in segments:
            fp = seg.get("footage_path")
            if fp and not Path(fp).exists():
                fname = PureWindowsPath(fp).name
                fallback = clips_dir / fname
                if fallback.exists():
                    seg["footage_path"] = str(fallback)
    else:
        print("STAGE 2: Footage Retrieval")
        print("=" * 60)
        segments = find_footage_for_segments(segments, clips_dir)
        _save_json(segments, "2_segments_with_footage.json")

    # Generate credits file for Pexels attribution
    _generate_credits(segments, credits_dir)

    # ──────────────────────────────────────────────
    # Stage 3: Voiceover Generation + Timestamps
    # ──────────────────────────────────────────────
    audio_path = audio_dir / "narration.mp3"
    print("\n" + "=" * 60)
    if (
        _has_checkpoint("3_segments_with_timing.json")
        and _has_checkpoint("3_alignment.json")
        and audio_path.exists()
    ):
        print("STAGE 3: Voiceover Generation [CACHED — skipping]")
        segments = _load_json(project_dir / "3_segments_with_timing.json")
        alignment = _load_json(project_dir / "3_alignment.json")
    else:
        print("STAGE 3: Voiceover Generation")
        print("=" * 60)

        # Reconstruct the full script from segments to ensure alignment
        full_script = " ".join(seg["text"] for seg in segments)
        audio_path, alignment = generate_voiceover(full_script, audio_dir)
        _save_json(alignment, "3_alignment.json")

        # Map timing back onto segments
        segments = map_segments_to_time_ranges(segments, alignment, audio_path)
        _save_json(segments, "3_segments_with_timing.json")

    # ──────────────────────────────────────────────
    # Stage 3.5: Caption Generation (opt-in)
    # ──────────────────────────────────────────────
    srt_path = None
    if captions and alignment:
        # Vertical videos use shorter cues (fewer words) since the frame is narrow
        cue_words = 5 if vertical else 8
        srt_path = project_dir / "captions.srt"
        if srt_path.exists() and not fresh:
            print("\n" + "=" * 60)
            print("STAGE 3.5: Caption Generation [CACHED — skipping]")
        else:
            print("\n" + "=" * 60)
            print("STAGE 3.5: Caption Generation")
            print("=" * 60)
            srt_path = generate_srt(alignment, srt_path, words_per_cue=cue_words)

    # ──────────────────────────────────────────────
    # Stage 4a: Timeline Assembly (AI → EDL)
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    if _has_checkpoint("4_edl.json"):
        print("STAGE 4: Timeline Assembly [CACHED — skipping]")
        edl = _load_json(project_dir / "4_edl.json")
    else:
        print("STAGE 4: Timeline Assembly")
        print("=" * 60)
        edl = build_timeline(segments)
        _save_json(edl, "4_edl.json")

    # ──────────────────────────────────────────────
    # Stage 4.5: Merge overlay paths into EDL (opt-in)
    # ──────────────────────────────────────────────
    if overlays:
        # The timeline builder doesn't know about overlays — we attach them
        # to EDL entries here by matching on segment_id.
        overlay_lookup = {
            seg["segment_id"]: seg.get("overlay_path")
            for seg in segments if seg.get("overlay_path")
        }
        if overlay_lookup:
            for entry in edl:
                sid = entry.get("segment_id")
                if sid in overlay_lookup:
                    entry["overlay_path"] = overlay_lookup[sid]
            print(f"[Pipeline] Attached {len(overlay_lookup)} overlay(s) to EDL entries.")

    # ──────────────────────────────────────────────
    # Stage 4b + 5: Video Assembly & Rendering
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STAGE 5: Video Rendering")
    print("=" * 60)
    output_name = _next_version_name(output_dir, project_name)
    output_path = assemble_video(
        edl, audio_path, output_dir, output_name,
        quality=quality, clips_dir=clips_dir, srt_path=srt_path,
        vertical=vertical,
    )

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
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore cached intermediate files and re-run all stages.",
    )
    parser.add_argument(
        "--quality",
        choices=["draft", "final"],
        default="final",
        help="Render quality: 'draft' for fast renders (~3-5x faster), 'final' for production (default).",
    )
    parser.add_argument(
        "--overlays",
        action="store_true",
        help="Enable text overlays for quotes, statistics, and citations (experimental, off by default).",
    )
    parser.add_argument(
        "--captions",
        action="store_true",
        help="Burn closed captions into the video, synced to the narrator's speech.",
    )
    parser.add_argument(
        "--vertical",
        action="store_true",
        help="Render in vertical format (1080x1920) for TikTok/Reels/YouTube Shorts.",
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

    # ── Vertical mode: override resolution and auto-enable captions ──
    if args.vertical:
        import src.config as _cfg
        _cfg.OUTPUT_WIDTH = 1080
        _cfg.OUTPUT_HEIGHT = 1920
        print("[Config] Vertical mode: 1080x1920 (9:16)")
        # Vertical short-form content should always have captions
        if not args.captions:
            args.captions = True
            print("[Config] Auto-enabling captions for vertical format")

    project_name = _derive_project_name(script_file, script_text)
    run_pipeline(
        script_text, project_name,
        fresh=args.fresh, quality=args.quality,
        overlays=args.overlays, captions=args.captions,
        vertical=args.vertical,
    )


if __name__ == "__main__":
    main()
