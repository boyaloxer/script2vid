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
from src.pipeline.script_analyzer import analyze_script
from src.pipeline.footage_finder import find_footage_for_segments
from src.pipeline.voiceover import generate_voiceover, map_segments_to_time_ranges
from src.pipeline.text_overlay import generate_overlays_for_segments
from src.pipeline.captions import generate_srt
from src.pipeline.timeline_builder import build_timeline
from src.pipeline.video_assembler import assemble_video
from src.publishing.publisher import upload_to_youtube
from src.publishing.calendar_manager import (
    auto_assign as calendar_auto_assign,
    load_calendar,
    update_slot,
)


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
    channel: str | None = None,
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
        channel: Calendar channel ID. When provided, the workspace is created
            under channels/<channel>/ instead of workspace/.

    Returns:
        Path to the rendered output video.
    """
    total_start = time.time()

    # Create per-script workspace folders (channel-based or legacy)
    paths = create_project_dirs(project_name, channel=channel)
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

    # ── Progress tracker ──
    # Writes _progress.json to the project dir at every stage boundary.
    # Readable by external tools regardless of stdout buffering.
    _progress_path = project_dir / "_progress.json"
    _progress_data = {
        "project_name": project_name,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages": {},
        "current_stage": None,
        "status": "running",
        "output_path": None,
    }

    def _update_progress(stage: str, status: str, detail: str | None = None):
        _progress_data["current_stage"] = stage
        _progress_data["stages"][stage] = {
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": round(time.time() - total_start, 1),
        }
        if detail:
            _progress_data["stages"][stage]["detail"] = detail
        _progress_path.write_text(
            json.dumps(_progress_data, indent=2, default=str),
            encoding="utf-8",
        )

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
        _update_progress("1_script_analysis", "cached", f"{len(segments)} segments")
    else:
        print("STAGE 1: Script Analysis")
        print("=" * 60)
        _update_progress("1_script_analysis", "running")
        segments = analyze_script(script_text)
        _save_json(segments, "1_segments.json")
        _update_progress("1_script_analysis", "done", f"{len(segments)} segments")

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
        _update_progress("2_footage_retrieval", "cached", f"{len(segments)} clips")
    else:
        print("STAGE 2: Footage Retrieval (Pexels)")
        print("=" * 60)
        _update_progress("2_footage_retrieval", "running")
        segments = find_footage_for_segments(segments, clips_dir)

        _save_json(segments, "2_segments_with_footage.json")
        _update_progress("2_footage_retrieval", "done", f"{len(segments)} clips")

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
        _update_progress("3_voiceover", "cached")
    else:
        print("STAGE 3: Voiceover Generation")
        print("=" * 60)
        _update_progress("3_voiceover", "running")

        # Reconstruct the full script from segments to ensure alignment
        full_script = " ".join(seg["text"] for seg in segments)
        audio_path, alignment = generate_voiceover(full_script, audio_dir)
        _save_json(alignment, "3_alignment.json")

        # Map timing back onto segments
        segments = map_segments_to_time_ranges(segments, alignment, audio_path)
        _save_json(segments, "3_segments_with_timing.json")
        _update_progress("3_voiceover", "done")

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
            _update_progress("3.5_captions", "cached")
        else:
            print("\n" + "=" * 60)
            print("STAGE 3.5: Caption Generation")
            print("=" * 60)
            _update_progress("3.5_captions", "running")
            srt_path = generate_srt(alignment, srt_path, words_per_cue=cue_words)
            _update_progress("3.5_captions", "done")

    # ──────────────────────────────────────────────
    # Stage 4a: Timeline Assembly (AI → EDL)
    # ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    if _has_checkpoint("4_edl.json"):
        print("STAGE 4: Timeline Assembly [CACHED — skipping]")
        edl = _load_json(project_dir / "4_edl.json")
        _update_progress("4_timeline", "cached", f"{len(edl)} EDL entries")
    else:
        print("STAGE 4: Timeline Assembly")
        print("=" * 60)
        _update_progress("4_timeline", "running")
        edl = build_timeline(segments)
        _save_json(edl, "4_edl.json")
        _update_progress("4_timeline", "done", f"{len(edl)} EDL entries")

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
    cached_output = _load_json(project_dir / "5_output.json") if not fresh else None
    if cached_output and Path(cached_output["output_path"]).exists():
        print("STAGE 5: Video Rendering [CACHED — skipping]")
        output_path = Path(cached_output["output_path"])
        _update_progress("5_rendering", "cached", str(output_path))
    else:
        print("STAGE 5: Video Rendering")
        print("=" * 60)
        _update_progress("5_rendering", "running")
        output_name = _next_version_name(output_dir, project_name)
        output_path = assemble_video(
            edl, audio_path, output_dir, output_name,
            quality=quality, clips_dir=clips_dir, srt_path=srt_path,
            vertical=vertical,
        )
        _save_json({"output_path": str(output_path)}, "5_output.json")
        _update_progress("5_rendering", "done", str(output_path))

    elapsed = time.time() - total_start
    _progress_data["status"] = "complete"
    _progress_data["output_path"] = str(output_path)
    _progress_data["elapsed_s"] = round(elapsed, 1)
    _update_progress("pipeline", "complete", f"{elapsed:.1f}s total")

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
    parser.add_argument(
        "--channel",
        type=str,
        default=None,
        help="Calendar channel ID — the single switch for scheduled publishing. "
             "Routes workspace to channels/<id>/, auto-assigns to the next open "
             "calendar slot, uploads to YouTube with the scheduled time, and "
             "updates the calendar. Pulls channel defaults (category, tags, "
             "vertical) automatically. (e.g. '--channel deep_thoughts').",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Upload the rendered video to YouTube after pipeline completes.",
    )
    parser.add_argument(
        "--schedule",
        type=str,
        default=None,
        help="Schedule YouTube publish time (ISO 8601, e.g. '2026-02-16T14:00:00Z'). Implies --publish.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="YouTube video title. If not provided, uses the project name.",
    )
    parser.add_argument(
        "--description",
        type=str,
        default="",
        help="YouTube video description.",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help="Comma-separated YouTube tags (e.g. 'Finance,Money,Shorts').",
    )
    parser.add_argument(
        "--category",
        type=str,
        default="people",
        help="YouTube category (e.g. 'people', 'education', 'entertainment'). Default: 'people'.",
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "private", "unlisted"],
        default="private",
        help="YouTube privacy status. Default: 'private'. Must be 'private' for scheduled publishing.",
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

    # ── Resolve channel defaults (if --channel is provided) ──
    channel_config = None
    if args.channel:
        from src.config import CHANNELS_DIR

        cal = load_calendar()
        channel_config = cal.get("channels", {}).get(args.channel)
        if not channel_config:
            print(f"Error: Channel '{args.channel}' not found in calendar.")
            print("Available channels:")
            for ch_id in cal.get("channels", {}):
                print(f"  - {ch_id}")
            print("\nAdd one with: python -m src.publishing.calendar_manager add-channel ...")
            sys.exit(1)

        # Load channel default_settings.json (pipeline switch defaults)
        defaults_path = CHANNELS_DIR / args.channel / "default_settings.json"
        if defaults_path.exists():
            defaults = _load_json(defaults_path) or {}
            print(f"[Channel] Loaded defaults from {defaults_path.name}")

            # Apply defaults for flags that were NOT explicitly set on the CLI.
            # argparse stores False for store_true flags that weren't passed,
            # so we detect "user didn't pass it" by checking sys.argv.
            # Explicit CLI flags always win.
            _raw = sys.argv

            # Boolean pipeline flags
            if not any(x in _raw for x in ("--vertical",))   and "vertical" in defaults:
                args.vertical = defaults["vertical"]
            if not any(x in _raw for x in ("--captions",))    and "captions" in defaults:
                args.captions = defaults["captions"]
            if not any(x in _raw for x in ("--overlays",))    and "overlays" in defaults:
                args.overlays = defaults["overlays"]
            if not any(x in _raw for x in ("--fresh",))       and "fresh" in defaults:
                args.fresh = defaults["fresh"]
            if not any(x in _raw for x in ("--quality",))     and "quality" in defaults:
                args.quality = defaults["quality"]

            # Publishing flags
            if not any(x in _raw for x in ("--publish",))     and "publish" in defaults:
                args.publish = defaults["publish"]
            if not any(x in _raw for x in ("--privacy",))     and "privacy" in defaults:
                args.privacy = defaults["privacy"]
            if not any(x in _raw for x in ("--category",))    and "category" in defaults:
                args.category = defaults["category"]
            if not any(x in _raw for x in ("--tags",))        and "tags" in defaults:
                args.tags = defaults["tags"]
            if not any(x in _raw for x in ("--title",))       and "title" in defaults:
                args.title = defaults["title"]
            if not any(x in _raw for x in ("--description",)) and "description" in defaults:
                args.description = defaults["description"]

            applied = []
            if defaults.get("vertical") and args.vertical:  applied.append("vertical")
            if defaults.get("captions") and args.captions:  applied.append("captions")
            if defaults.get("overlays") and args.overlays:  applied.append("overlays")
            if defaults.get("publish") and args.publish:    applied.append("publish")
            if applied:
                print(f"[Channel] Defaults applied: {', '.join(applied)}")
        else:
            print(f"[Channel] No default_settings.json found — using CLI flags only.")

        # Pull channel defaults for category/tags (CLI flags override)
        if not args.category or args.category == "people":
            args.category = channel_config.get("default_category", args.category)
        if not args.tags and channel_config.get("default_tags"):
            args.tags = ",".join(channel_config["default_tags"])

    # ── Vertical mode: override resolution ──
    if args.vertical:
        import src.config as _cfg
        _cfg.OUTPUT_WIDTH = 1080
        _cfg.OUTPUT_HEIGHT = 1920
        print("[Config] Vertical mode: 1080x1920 (9:16)")

    # --schedule implies --publish (for manual publishing without --channel)
    if args.schedule:
        args.publish = True

    project_name = _derive_project_name(script_file, script_text)
    output_path = run_pipeline(
        script_text, project_name,
        fresh=args.fresh, quality=args.quality,
        overlays=args.overlays, captions=args.captions,
        vertical=args.vertical,
        channel=args.channel,
    )

    # ══════════════════════════════════════════════════════════════
    #  Post-render: Calendar assignment + YouTube publishing
    # ══════════════════════════════════════════════════════════════

    # Check for existing publish checkpoint to avoid duplicate uploads
    project_dir = output_path.parent.parent
    published_checkpoint = project_dir / "6_published.json"
    already_published = None
    if published_checkpoint.exists() and not args.fresh:
        try:
            already_published = json.loads(
                published_checkpoint.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            pass

    if already_published:
        print("\n" + "=" * 60)
        print("STAGE 6: Publishing [ALREADY DONE — skipping]")
        yt_url = already_published.get("youtube_url", "N/A")
        slot_time = already_published.get("scheduled_time", "N/A")
        print(f"  YouTube: {yt_url}")
        print(f"  Scheduled: {slot_time}")
        print("  (Use --fresh to force re-upload)")
        return

    yt_title = args.title or project_name.replace("_", " ").title()
    yt_tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    if args.channel:
        # ── Calendar assignment (always happens with --channel) ──
        print("\n" + "=" * 60)
        print("STAGE 6a: Calendar — Assigning to next open slot")
        print("=" * 60)
        slot = calendar_auto_assign(
            channel_id=args.channel,
            video_path=str(output_path),
            title=yt_title,
            description=args.description or None,
            tags=yt_tags or None,
            workspace=str(output_path.parent.parent),
            is_vertical=args.vertical,
        )

        if not slot:
            print("  No open slots available.")
            print("  Run 'python -m src.publishing.calendar_manager generate' to create more.")
            print(f"  Video still saved at: {output_path}")
        else:
            print(f"  Slot:      {slot['id']}")
            print(f"  Scheduled: {slot['scheduled_time']}")

            # ── Upload to YouTube (only if publish is enabled) ──
            if args.publish:
                print("\n" + "=" * 60)
                print("STAGE 6b: YouTube — Uploading with scheduled release")
                print("=" * 60)
                try:
                    result = upload_to_youtube(
                        video_path=output_path,
                        title=yt_title,
                        description=args.description or "",
                        tags=yt_tags,
                        category=args.category,
                        privacy="private",  # Required for scheduled publishing
                        publish_at=slot["scheduled_time"],
                        is_short=args.vertical,
                        contains_synthetic_media=False,
                        channel_id=args.channel,
                    )

                    # Update calendar to "uploaded"
                    update_slot(
                        slot["id"],
                        status="uploaded",
                        youtube_video_id=result["video_id"],
                        youtube_url=result["url"],
                    )
                    print(f"\n  [Calendar] Slot {slot['id']} -> uploaded")
                    print(f"  [Calendar] Will auto-publish at {slot['scheduled_time']}")

                    # Save publish checkpoint
                    published_checkpoint.write_text(json.dumps({
                        "slot_id": slot["id"],
                        "scheduled_time": slot["scheduled_time"],
                        "youtube_video_id": result["video_id"],
                        "youtube_url": result["url"],
                        "title": yt_title,
                        "output_path": str(output_path),
                    }, indent=2), encoding="utf-8")

                except FileNotFoundError as e:
                    print(f"\n{e}")
                    print("  YouTube upload skipped — video assigned to calendar slot.")
                    print("  Run 'python -m src.publishing.calendar_manager publish-due' later.")
                except Exception as e:
                    print(f"\n[YouTube] Upload failed: {e}")
                    print("  Video is assigned to the calendar slot.")
                    print("  Run 'python -m src.publishing.calendar_manager publish-due' to retry.")
            else:
                print(f"\n  [Calendar] Slot assigned. Publishing is off for this channel.")
                print(f"  To upload later: python -m src.publishing.calendar_manager publish-due")

    elif args.publish:
        # ── Standalone upload (no channel / no calendar) ──
        print("\n" + "=" * 60)
        print("STAGE 6: YouTube Publishing")
        print("=" * 60)
        try:
            result = upload_to_youtube(
                video_path=output_path,
                title=yt_title,
                description=args.description,
                tags=yt_tags,
                category=args.category,
                privacy=args.privacy,
                publish_at=args.schedule,
                is_short=args.vertical,
                contains_synthetic_media=False,
                channel_id=args.channel,
            )

            # Save publish checkpoint
            published_checkpoint.write_text(json.dumps({
                "youtube_video_id": result["video_id"],
                "youtube_url": result["url"],
                "title": yt_title,
                "output_path": str(output_path),
            }, indent=2), encoding="utf-8")

        except FileNotFoundError as e:
            print(f"\n{e}")
            print("Skipping YouTube upload. Video was still rendered successfully.")
        except Exception as e:
            print(f"\n[YouTube] Upload failed: {e}")
            print("Video was still rendered successfully at:")
            print(f"  {output_path}")


if __name__ == "__main__":
    main()
