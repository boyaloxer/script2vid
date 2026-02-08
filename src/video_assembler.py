"""
Stage 4b + 5 — Video Assembly & Rendering  (FFmpeg-direct)

Uses FFmpeg subprocess calls for ALL heavy lifting:
1. Each clip: trim + speed-adjust + scale + crop  →  one temp .mp4
2. All temp clips joined via FFmpeg concat demuxer  (no re-encode)
3. Narration audio overlaid via FFmpeg              (no video re-encode)

This bypasses MoviePy's slow frame-by-frame Python path entirely,
cutting render time by ~10-20×.  Memory usage is minimal — only one
FFmpeg subprocess runs at a time.
"""

import json
import os
import subprocess
from pathlib import Path

from src.config import OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS


# ── Helpers ───────────────────────────────────────────────────────────────

def _probe_duration(path: str) -> float:
    """Get the duration of a media file via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _probe_resolution(path: str) -> tuple[int, int]:
    """Get (width, height) of the first video stream via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        w, h = result.stdout.strip().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 0, 0


def _build_scale_crop_filter(src_w: int, src_h: int) -> str:
    """
    Build an FFmpeg filter string that scales to *cover* the target
    resolution, then center-crops to the exact target size.
    Same logic as the old _resize_and_crop, but in FFmpeg filter syntax.
    """
    tw, th = OUTPUT_WIDTH, OUTPUT_HEIGHT

    if src_w <= 0 or src_h <= 0:
        # Can't calculate — just force the output size
        return f"scale={tw}:{th}:force_original_aspect_ratio=disable"

    scale = max(tw / src_w, th / src_h)
    # Scale to cover (round up to even numbers for codec compatibility)
    sw = int(src_w * scale)
    sh = int(src_h * scale)
    sw += sw % 2  # ensure even
    sh += sh % 2

    # Center crop to exact target
    return f"scale={sw}:{sh},crop={tw}:{th}"


def _ffmpeg_process_clip(
    entry: dict,
    index: int,
    total: int,
    tmp_dir: Path,
    preset: str,
    threads: int,
) -> Path:
    """
    Process one EDL entry entirely via FFmpeg:
      - Trim to footage_trim_start / footage_trim_end
      - Speed-adjust via setpts if trim duration != slot_duration
      - Scale + center-crop to OUTPUT_WIDTH x OUTPUT_HEIGHT
      - Strip audio (narrator-only pipeline)
      - Output a temp .mp4
    """
    seg_id = entry.get("segment_id", index + 1)
    target_dur = entry.get("slot_duration")
    if target_dur is None:
        target_dur = round(entry.get("audio_end", 1) - entry.get("audio_start", 0), 3)
    if target_dur <= 0:
        target_dur = 1.0

    out_path = tmp_dir / f"clip_{index:04d}.mp4"

    # ── No footage → black frame ──────────────────────────────────────
    if entry.get("footage_file") is None:
        print(f"  [{index + 1}/{total}] Segment {seg_id}: black frame ({target_dur:.2f}s)")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=black:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:r={OUTPUT_FPS}:d={target_dur}",
            "-c:v", "libx264", "-preset", preset,
            "-threads", str(threads),
            "-an",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [FFmpeg clip error] {result.stderr[-300:]}")
            raise RuntimeError(f"FFmpeg failed on black frame for segment {seg_id}")
        return out_path

    # ── Real footage ──────────────────────────────────────────────────
    src = entry["footage_file"]
    trim_start = entry.get("footage_trim_start", 0)
    trim_end = entry.get("footage_trim_end")

    # Probe source to get resolution (needed for scale+crop filter)
    src_w, src_h = _probe_resolution(src)

    # Compute trim duration and speed factor
    if trim_end is not None:
        trim_dur = trim_end - trim_start
    else:
        # No explicit end — probe the file
        total_dur = _probe_duration(src)
        trim_dur = max(total_dur - trim_start, 0.1)
        trim_end = trim_start + trim_dur

    # Clamp negatives
    trim_start = max(0, trim_start)
    trim_dur = max(0.1, trim_dur)

    # Speed adjustment: if trimmed segment != slot duration, change playback speed
    speed_factor = trim_dur / target_dur if target_dur > 0 else 1.0

    # Build video filter chain
    filters = []

    # 1. Speed adjustment via setpts (if needed)
    if abs(speed_factor - 1.0) > 0.01:
        # setpts=PTS/speed  → speed>1 = faster, speed<1 = slower
        # Clamp to sane range
        clamped = max(0.5, min(speed_factor, 2.0))
        filters.append(f"setpts=PTS/{clamped}")

    # 2. Scale + center-crop
    filters.append(_build_scale_crop_filter(src_w, src_h))

    # 3. Force constant frame rate
    filters.append(f"fps={OUTPUT_FPS}")

    vf = ",".join(filters)

    print(f"  [{index + 1}/{total}] Segment {seg_id}: "
          f"{trim_dur:.1f}s -> {target_dur:.1f}s "
          f"(speed {speed_factor:.2f}x)")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(trim_start),
        "-i", src,
        "-t", str(target_dur),   # Output duration = slot_duration
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-threads", str(threads),
        "-an",                    # Strip audio — narrator only
        "-movflags", "+faststart",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [FFmpeg clip error] {result.stderr[-500:]}")
        raise RuntimeError(f"FFmpeg failed on segment {seg_id}")

    return out_path


# ── Concat & audio helpers (unchanged, proven working) ────────────────────

def _ffmpeg_concat(clip_paths: list[Path], output_path: Path) -> None:
    """
    Concatenate pre-rendered clip files using FFmpeg concat demuxer.
    Nearly instant — no re-encoding since all clips share codec/resolution/fps.
    """
    list_file = output_path.parent / "_concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            safe = str(p).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ]
    print(f"[Video Assembler] FFmpeg concat: joining {len(clip_paths)} clips...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg concat stderr] {result.stderr}")
        raise RuntimeError(f"FFmpeg concat failed (exit {result.returncode})")

    list_file.unlink(missing_ok=True)


def _ffmpeg_add_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """
    Overlay narration audio onto the silent concatenated video.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-ac", "2",       # force stereo — prevents mono routing issues on some players
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        str(output_path),
    ]
    print("[Video Assembler] FFmpeg: overlaying narration audio...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg audio stderr] {result.stderr}")
        raise RuntimeError(f"FFmpeg audio overlay failed (exit {result.returncode})")


# ── Main entry point ──────────────────────────────────────────────────────

def assemble_video(
    edl: list[dict],
    audio_path: Path,
    output_dir: Path,
    output_name: str = "final_video.mp4",
    quality: str = "final",
) -> Path:
    """
    Execute the EDL using FFmpeg-direct processing.

    Each clip is processed by a single FFmpeg subprocess call
    (trim + speed + scale + crop), then all clips are joined via
    the concat demuxer and narration audio is overlaid — both without
    re-encoding the video stream.

    Memory usage is minimal: only one FFmpeg process at a time.
    Scales to 1+ hour videos with hundreds of clips.

    Args:
        edl: The Edit Decision List (list of dicts from timeline_builder)
        audio_path: Path to the narration audio file
        output_dir: Directory to save the output video
        output_name: Filename for the output video
        quality: "draft" for fast renders, "final" for production quality

    Returns:
        Path to the rendered video file.
    """
    presets = {
        "draft": "ultrafast",
        "final": "medium",
    }
    preset = presets.get(quality, "medium")
    threads = os.cpu_count() or 4

    print(f"[Video Assembler] Building video from {len(edl)} EDL entries (FFmpeg-direct)...")
    print(f"[Video Assembler] Quality: {quality} (preset={preset}, threads={threads})")

    # Sort EDL by slot_start to ensure correct ordering
    # (Hurdle #7: fallback to audio_start for backward compat)
    edl_sorted = sorted(edl, key=lambda e: e.get("slot_start", e.get("audio_start", 0)))

    if not edl_sorted:
        raise RuntimeError("No clips to assemble — EDL is empty.")

    tmp_dir = output_dir / "_render_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    clip_paths: list[Path] = []
    try:
        # ── Step 1: Process each clip via FFmpeg ──────────────────────
        total = len(edl_sorted)
        for i, entry in enumerate(edl_sorted):
            clip_path = _ffmpeg_process_clip(
                entry=entry,
                index=i,
                total=total,
                tmp_dir=tmp_dir,
                preset=preset,
                threads=threads,
            )
            clip_paths.append(clip_path)

        # ── Step 2: Concat all clips (no re-encode) ──────────────────
        silent_video = tmp_dir / "_concat_silent.mp4"
        _ffmpeg_concat(clip_paths, silent_video)

        # ── Step 3: Overlay narration audio (no video re-encode) ──────
        output_path = output_dir / output_name
        _ffmpeg_add_audio(silent_video, audio_path, output_path)

        print(f"[Video Assembler] Done! Output: {output_path}")
        return output_path

    finally:
        # Clean up temp directory
        print("[Video Assembler] Cleaning up temp files...")
        for f in tmp_dir.iterdir():
            f.unlink(missing_ok=True)
        tmp_dir.rmdir()
        print("[Video Assembler] Temp files removed.")
