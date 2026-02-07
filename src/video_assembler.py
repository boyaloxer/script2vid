"""
Stage 4b + 5 — Video Assembly & Rendering

Reads the Edit Decision List (EDL) and uses MoviePy to:
1. Trim each footage clip to the specified window
2. Resize to target resolution
3. Apply transitions (crossfades)
4. Concatenate clips in memory-safe batches
5. Join batches with FFmpeg concat demuxer (no re-encode)
6. Overlay the narration audio
7. Render the final MP4

Memory-safe: only ~BATCH_SIZE clips are in memory at any time.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from moviepy import (
    VideoFileClip,
    AudioFileClip,
    ColorClip,
    concatenate_videoclips,
    vfx,
)

from src.config import OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS

# Maximum clips to hold in RAM at once.  30 is safe for most machines.
_BATCH_SIZE = 30


def _load_and_trim_clip(entry: dict) -> VideoFileClip | ColorClip:
    """
    Load a footage clip and trim it according to the EDL entry.
    Returns a clip sized to fill the FULL slot duration (including silence
    between narration segments), so clips stay in sync with the audio.
    """
    # Use slot_duration (full slot) instead of just the speech duration
    target_dur = entry.get("slot_duration")
    if target_dur is None:
        # Fallback for old EDL format without slot info
        target_dur = round(entry["audio_end"] - entry["audio_start"], 3)

    # Safety: ensure positive duration
    if target_dur <= 0:
        target_dur = 1.0

    # If no footage, produce a black frame
    if entry.get("footage_file") is None:
        print(f"  Segment {entry['segment_id']}: No footage — using black frame.")
        return ColorClip(
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            color=(0, 0, 0),
        ).with_duration(target_dur).with_fps(OUTPUT_FPS)

    clip = VideoFileClip(entry["footage_file"])

    # Strip clip audio — only the narrator's voice should be heard
    clip = clip.without_audio()

    # Trim to the specified window
    trim_start = entry.get("footage_trim_start", 0)
    trim_end = entry.get("footage_trim_end", clip.duration)

    # Safety: clamp to actual clip bounds
    trim_start = max(0, min(trim_start, clip.duration - 0.1))
    trim_end = max(trim_start + 0.1, min(trim_end, clip.duration))

    clip = clip.subclipped(trim_start, trim_end)

    # If the trimmed clip doesn't match the needed slot duration, adjust speed
    trim_dur = clip.duration
    if abs(trim_dur - target_dur) > 0.1 and target_dur > 0:
        speed_factor = trim_dur / target_dur
        if 0.5 <= speed_factor <= 2.0:
            # Acceptable speed range — apply time stretch
            clip = clip.with_effects([vfx.MultiplySpeed(speed_factor)])
        else:
            # Too extreme — just force the duration (may freeze/skip frames)
            clip = clip.with_duration(target_dur)
    else:
        clip = clip.with_duration(target_dur)

    # Resize to target resolution (maintaining aspect ratio with crop)
    clip = _resize_and_crop(clip)

    return clip


def _resize_and_crop(clip: VideoFileClip) -> VideoFileClip:
    """
    Resize a clip to fill OUTPUT_WIDTH x OUTPUT_HEIGHT.
    Scales up to cover the frame, then center-crops.
    """
    target_w, target_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
    clip_w, clip_h = clip.size

    # Scale factor to fill the target (cover, not fit)
    scale = max(target_w / clip_w, target_h / clip_h)
    new_w = int(clip_w * scale)
    new_h = int(clip_h * scale)

    clip = clip.resized((new_w, new_h))

    # Center crop to exact target
    if new_w != target_w or new_h != target_h:
        x_offset = (new_w - target_w) // 2
        y_offset = (new_h - target_h) // 2
        clip = clip.cropped(
            x1=x_offset,
            y1=y_offset,
            x2=x_offset + target_w,
            y2=y_offset + target_h,
        )

    return clip


def _render_batch(
    batch_entries: list[dict],
    batch_idx: int,
    total_batches: int,
    global_offset: int,
    total_entries: int,
    tmp_dir: Path,
    preset: str,
    threads: int,
) -> Path:
    """
    Load, trim, and render one batch of clips to a temporary .mp4.
    Frees memory after rendering.
    """
    clips = []
    for i, entry in enumerate(batch_entries):
        global_i = global_offset + i + 1
        print(f"  [{global_i}/{total_entries}] Loading segment {entry['segment_id']}...")
        clip = _load_and_trim_clip(entry)

        # Apply crossfade-in if specified (skip for first clip of first batch)
        if (global_offset + i) > 0 and entry.get("transition") == "crossfade":
            fade_dur = entry.get("transition_duration", 0.3)
            clip = clip.with_effects([vfx.CrossFadeIn(fade_dur)])

        clips.append(clip)

    if not clips:
        raise RuntimeError(f"Batch {batch_idx + 1} produced no clips.")

    print(f"  [Batch {batch_idx + 1}/{total_batches}] Concatenating {len(clips)} clips...")
    batch_video = concatenate_videoclips(clips, method="compose")

    batch_path = tmp_dir / f"batch_{batch_idx:03d}.mp4"
    batch_video.write_videofile(
        str(batch_path),
        fps=OUTPUT_FPS,
        codec="libx264",
        audio_codec="aac",
        preset=preset,
        threads=threads,
        logger=None,  # Suppress per-batch progress bars to reduce noise
    )

    # Free memory immediately
    batch_video.close()
    for c in clips:
        c.close()
    clips.clear()

    print(f"  [Batch {batch_idx + 1}/{total_batches}] Rendered to {batch_path.name}")
    return batch_path


def _ffmpeg_concat(batch_paths: list[Path], output_path: Path) -> None:
    """
    Concatenate pre-rendered batch files using FFmpeg concat demuxer.
    This is nearly instant and doesn't re-encode, keeping memory flat.
    """
    # Build the concat list file
    list_file = output_path.parent / "_concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in batch_paths:
            # FFmpeg concat format: file 'path'
            safe = str(p).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",  # No re-encode
        str(output_path),
    ]
    print(f"[Video Assembler] FFmpeg concat: joining {len(batch_paths)} batches...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FFmpeg concat stderr] {result.stderr}")
        raise RuntimeError(f"FFmpeg concat failed (exit {result.returncode})")

    # Clean up temp list file
    list_file.unlink(missing_ok=True)


def _ffmpeg_add_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """
    Overlay narration audio onto the silent concatenated video.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",  # No re-encode of video
        "-c:a", "aac",
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


def assemble_video(
    edl: list[dict],
    audio_path: Path,
    output_dir: Path,
    output_name: str = "final_video.mp4",
    quality: str = "final",
) -> Path:
    """
    Execute the EDL: load clips in batches, render batch files,
    concatenate with FFmpeg, overlay narration audio.

    Memory-safe: only ~BATCH_SIZE clips are in memory at any time,
    so this scales to 1+ hour videos with hundreds of clips.

    Args:
        edl: The Edit Decision List (list of dicts from timeline_builder)
        audio_path: Path to the narration audio file
        output_dir: Directory to save the output video
        output_name: Filename for the output video
        quality: "draft" for fast renders (lower quality, ~3-5x faster),
                 "final" for production quality (default)

    Returns:
        Path to the rendered video file.
    """
    # Render presets: draft is much faster, final is higher quality
    presets = {
        "draft": "ultrafast",
        "final": "medium",
    }
    preset = presets.get(quality, "medium")
    threads = os.cpu_count() or 4

    print(f"[Video Assembler] Building video from {len(edl)} EDL entries...")
    print(f"[Video Assembler] Quality: {quality} (preset={preset}, threads={threads})")

    # Sort EDL by slot_start to ensure correct ordering
    edl_sorted = sorted(edl, key=lambda e: e.get("slot_start", e.get("audio_start", 0)))

    if not edl_sorted:
        raise RuntimeError("No clips to assemble — EDL is empty.")

    # ── Small EDL: single-pass (original behaviour, no temp files) ────────
    if len(edl_sorted) <= _BATCH_SIZE:
        return _assemble_single_pass(edl_sorted, audio_path, output_dir, output_name, preset, threads)

    # ── Large EDL: chunked rendering ─────────────────────────────────────
    return _assemble_chunked(edl_sorted, audio_path, output_dir, output_name, preset, threads)


def _assemble_single_pass(
    edl_sorted: list[dict],
    audio_path: Path,
    output_dir: Path,
    output_name: str,
    preset: str,
    threads: int,
) -> Path:
    """Original single-pass render for small EDLs (<=BATCH_SIZE clips)."""
    clips = []
    for i, entry in enumerate(edl_sorted):
        print(f"  [{i + 1}/{len(edl_sorted)}] Loading segment {entry['segment_id']}...")
        clip = _load_and_trim_clip(entry)
        if i > 0 and entry.get("transition") == "crossfade":
            fade_dur = entry.get("transition_duration", 0.3)
            clip = clip.with_effects([vfx.CrossFadeIn(fade_dur)])
        clips.append(clip)

    print("[Video Assembler] Concatenating clips...")
    video = concatenate_videoclips(clips, method="compose")

    print("[Video Assembler] Overlaying narration audio...")
    narration = AudioFileClip(str(audio_path))
    video = video.with_audio(narration)

    output_path = output_dir / output_name
    print(f"[Video Assembler] Rendering to {output_path}...")
    video.write_videofile(
        str(output_path),
        fps=OUTPUT_FPS,
        codec="libx264",
        audio_codec="aac",
        preset=preset,
        threads=threads,
    )

    video.close()
    narration.close()
    for c in clips:
        c.close()

    print(f"[Video Assembler] Done! Output: {output_path}")
    return output_path


def _assemble_chunked(
    edl_sorted: list[dict],
    audio_path: Path,
    output_dir: Path,
    output_name: str,
    preset: str,
    threads: int,
) -> Path:
    """
    Memory-safe chunked rendering for large EDLs.
    Renders batches of ~BATCH_SIZE clips to temp files, then uses
    FFmpeg concat demuxer to join them without re-encoding.
    """
    total = len(edl_sorted)
    num_batches = (total + _BATCH_SIZE - 1) // _BATCH_SIZE
    print(f"[Video Assembler] Chunked render: {total} clips in {num_batches} batches of ~{_BATCH_SIZE}")

    tmp_dir = output_dir / "_render_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    batch_paths: list[Path] = []
    try:
        for b in range(num_batches):
            start = b * _BATCH_SIZE
            end = min(start + _BATCH_SIZE, total)
            batch_entries = edl_sorted[start:end]

            batch_path = _render_batch(
                batch_entries=batch_entries,
                batch_idx=b,
                total_batches=num_batches,
                global_offset=start,
                total_entries=total,
                tmp_dir=tmp_dir,
                preset=preset,
                threads=threads,
            )
            batch_paths.append(batch_path)

        # Concatenate batches with FFmpeg (no re-encode)
        silent_video = tmp_dir / "_concat_silent.mp4"
        _ffmpeg_concat(batch_paths, silent_video)

        # Overlay narration audio
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
