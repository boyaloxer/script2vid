"""
Stage 4b + 5 — Video Assembly & Rendering  (FFmpeg-direct)

Uses FFmpeg subprocess calls for ALL heavy lifting:
1. Each clip: trim + speed-adjust + scale + crop  →  one temp .mp4
   - If the clip has a text overlay PNG, it is composited on top with
     a fade-in / fade-out animation via FFmpeg's overlay filter.
2. All temp clips joined via FFmpeg concat demuxer  (no re-encode)
3. Narration audio overlaid via FFmpeg              (no video re-encode)

This bypasses MoviePy's slow frame-by-frame Python path entirely,
cutting render time by ~10-20×.  Memory usage is minimal — only one
FFmpeg subprocess runs at a time.
"""

import os
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

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


def _build_overlay_fade_chain(
    target_dur: float,
    fade_in: float = 0.4,
    fade_out: float = 0.4,
    delay: float = 0.3,
) -> str:
    """
    Build the FFmpeg filter chain to apply to the overlay PNG before
    compositing: format conversion + fade-in + fade-out on the alpha channel.

    Uses FFmpeg's `fade` filter with `alpha=1` to modify only the
    transparency channel, producing a smooth appear/disappear effect.

    Args:
        target_dur: Total clip duration in seconds.
        fade_in:  Duration of the fade-in (seconds).
        fade_out: Duration of the fade-out (seconds).
        delay:    Seconds before the overlay starts appearing.

    Returns:
        A filter chain string like:
        "format=rgba,fade=t=in:st=0.3:d=0.4:alpha=1,fade=t=out:st=2.4:d=0.4:alpha=1"
    """
    t_fade_out_start = max(target_dur - fade_out - 0.2, delay + fade_in + 0.5)

    return (
        f"format=rgba,"
        f"fade=t=in:st={delay:.2f}:d={fade_in:.2f}:alpha=1,"
        f"fade=t=out:st={t_fade_out_start:.2f}:d={fade_out:.2f}:alpha=1"
    )


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
      - If overlay_path is present, composite the text overlay PNG on top
        with a fade-in / fade-out animation
      - Strip audio (narrator-only pipeline)
      - Output a temp .mp4
    """
    seg_id = entry.get("segment_id", index + 1)
    target_dur = entry.get("slot_duration")
    if target_dur is None:
        target_dur = round(entry.get("audio_end", 1) - entry.get("audio_start", 0), 3)
    if target_dur <= 0:
        target_dur = 1.0

    overlay_path = entry.get("overlay_path")
    has_overlay = overlay_path and Path(overlay_path).exists()

    out_path = tmp_dir / f"clip_{index:04d}.mp4"

    # ── No footage → black frame ──────────────────────────────────────
    if entry.get("footage_file") is None:
        label = "black frame"
        if has_overlay:
            label += " + overlay"
        print(f"  [{index + 1}/{total}] Segment {seg_id}: {label} ({target_dur:.2f}s)")

        if has_overlay:
            # Black frame + overlay composite with fade animation
            ovr_filters = _build_overlay_fade_chain(target_dur)
            fc = (
                f"[1:v]{ovr_filters}[ovr];"
                f"[0:v][ovr]overlay=0:0:format=auto"
            )
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                f"color=c=black:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:r={OUTPUT_FPS}:d={target_dur}",
                "-loop", "1", "-t", str(target_dur), "-i", overlay_path,
                "-filter_complex", fc,
                "-c:v", "libx264", "-preset", preset,
                "-threads", str(threads),
                "-t", str(target_dur),
                "-an",
                str(out_path),
            ]
        else:
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

    overlay_tag = " + overlay" if has_overlay else ""
    print(f"  [{index + 1}/{total}] Segment {seg_id}: "
          f"{trim_dur:.1f}s -> {target_dur:.1f}s "
          f"(speed {speed_factor:.2f}x){overlay_tag}")

    if has_overlay:
        # ── Footage + overlay composite ────────────────────────────────
        # filter_complex: process video → [base], then composite PNG
        # overlay on top with a fade-in / fade-out alpha animation.
        vf_chain = ",".join(filters)
        ovr_filters = _build_overlay_fade_chain(target_dur)

        fc = (
            f"[0:v]{vf_chain}[base];"
            f"[1:v]{ovr_filters}[ovr];"
            f"[base][ovr]overlay=0:0:format=auto"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(trim_start),
            "-i", src,
            "-loop", "1", "-t", str(target_dur),
            "-i", overlay_path,
            "-filter_complex", fc,
            "-t", str(target_dur),
            "-c:v", "libx264",
            "-preset", preset,
            "-threads", str(threads),
            "-an",
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        # ── Footage only (no overlay) ──────────────────────────────────
        vf = ",".join(filters)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(trim_start),
            "-i", src,
            "-t", str(target_dur),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", preset,
            "-threads", str(threads),
            "-an",
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


def _ffmpeg_burn_captions(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    preset: str = "medium",
    threads: int = 4,
) -> None:
    """
    Burn SRT subtitles into the video as a final pass.

    Uses FFmpeg's subtitles filter with force_style for a clean,
    modern look: white text, semi-transparent dark background,
    positioned at the bottom center.
    """
    # FFmpeg subtitles filter needs forward slashes and escaped colons/backslashes
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")

    # Clean, modern subtitle style:
    #   Fontname: Arial/sans-serif, size 22, white, semi-transparent dark box
    #   Bottom center, small margin from edge
    style = (
        "Fontname=Arial,Fontsize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H40000000,BackColour=&H80000000,"
        "BorderStyle=4,Outline=0,Shadow=0,"
        "MarginV=35,Alignment=2"
    )

    vf = f"subtitles='{srt_escaped}':force_style='{style}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-c:a", "copy",
        "-threads", str(threads),
        str(output_path),
    ]
    print("[Video Assembler] FFmpeg: burning in captions...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg captions stderr] {result.stderr[-500:]}")
        raise RuntimeError(f"FFmpeg caption burn-in failed (exit {result.returncode})")


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

def _resolve_footage_path(path_str: str, clips_dir: Path | None) -> str:
    """
    If path_str doesn't exist (e.g. workspace copied from another OS),
    try the same filename in clips_dir so resume still works.

    Uses PureWindowsPath to extract the filename because it handles both
    / and \\ separators on any OS, whereas PosixPath treats \\ as literal.
    """
    if Path(path_str).exists():
        return path_str
    if clips_dir:
        fname = PureWindowsPath(path_str).name
        fallback = clips_dir / fname
        if fallback.exists():
            return str(fallback)
    return path_str


def assemble_video(
    edl: list[dict],
    audio_path: Path,
    output_dir: Path,
    output_name: str = "final_video.mp4",
    quality: str = "final",
    clips_dir: Path | None = None,
    srt_path: Path | None = None,
) -> Path:
    """
    Execute the EDL using FFmpeg-direct processing.

    Each clip is processed by a single FFmpeg subprocess call
    (trim + speed + scale + crop), then all clips are joined via
    the concat demuxer and narration audio is overlaid — both without
    re-encoding the video stream.

    If srt_path is provided, a final pass burns subtitles into the video.

    Memory usage is minimal: only one FFmpeg process at a time.
    Scales to 1+ hour videos with hundreds of clips.

    Args:
        edl: The Edit Decision List (list of dicts from timeline_builder)
        audio_path: Path to the narration audio file
        output_dir: Directory to save the output video
        output_name: Filename for the output video
        quality: "draft" for fast renders, "final" for production quality
        clips_dir: Project clips directory; used to resolve footage paths
            when they were saved on another OS (e.g. Windows → Mac).
        srt_path: Optional path to an SRT subtitle file. If provided,
            captions are burned into the final video.

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

    # Resolve footage paths if workspace was copied from another OS
    for entry in edl_sorted:
        if entry.get("footage_file"):
            entry["footage_file"] = _resolve_footage_path(
                entry["footage_file"], clips_dir
            )

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
        if srt_path and srt_path.exists():
            # Captions enabled — audio overlay goes to a temp file,
            # then we burn in subtitles as the final step
            with_audio = tmp_dir / "_with_audio.mp4"
            _ffmpeg_add_audio(silent_video, audio_path, with_audio)

            # ── Step 4: Burn in captions (re-encodes video) ───────
            output_path = output_dir / output_name
            _ffmpeg_burn_captions(
                with_audio, srt_path, output_path,
                preset=preset, threads=threads,
            )
        else:
            output_path = output_dir / output_name
            _ffmpeg_add_audio(silent_video, audio_path, output_path)

        print(f"[Video Assembler] Done! Output: {output_path}")
        return output_path

    finally:
        # Clean up temp directory — use shutil.rmtree to handle macOS
        # .DS_Store files and any other OS-created artifacts
        print("[Video Assembler] Cleaning up temp files...")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[Video Assembler] Temp files removed.")
