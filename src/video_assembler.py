"""
Stage 4b + 5 — Video Assembly & Rendering

Reads the Edit Decision List (EDL) and uses MoviePy to:
1. Trim each footage clip to the specified window
2. Resize to target resolution
3. Apply transitions (crossfades)
4. Concatenate all clips in sequence
5. Overlay the narration audio
6. Render the final MP4
"""

from pathlib import Path
from moviepy import (
    VideoFileClip,
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    CompositeAudioClip,
    concatenate_videoclips,
    vfx,
)

from src.config import OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, OUTPUT_DIR


def _load_and_trim_clip(entry: dict) -> VideoFileClip | ColorClip:
    """
    Load a footage clip and trim it according to the EDL entry.
    Returns a clip sized to fill the target audio duration.
    """
    audio_dur = round(entry["audio_end"] - entry["audio_start"], 3)

    # If no footage, produce a black frame
    if entry.get("footage_file") is None:
        print(f"  Segment {entry['segment_id']}: No footage — using black frame.")
        return ColorClip(
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            color=(0, 0, 0),
        ).with_duration(audio_dur).with_fps(OUTPUT_FPS)

    clip = VideoFileClip(entry["footage_file"])

    # Trim to the specified window
    trim_start = entry.get("footage_trim_start", 0)
    trim_end = entry.get("footage_trim_end", clip.duration)

    # Safety: clamp to actual clip bounds
    trim_start = max(0, min(trim_start, clip.duration - 0.1))
    trim_end = max(trim_start + 0.1, min(trim_end, clip.duration))

    clip = clip.subclipped(trim_start, trim_end)

    # If the trimmed clip doesn't match the needed duration, adjust speed
    trim_dur = clip.duration
    if abs(trim_dur - audio_dur) > 0.1 and audio_dur > 0:
        speed_factor = trim_dur / audio_dur
        if 0.5 <= speed_factor <= 2.0:
            # Acceptable speed range — apply time stretch
            clip = clip.with_effects([vfx.MultiplySpeed(speed_factor)])
        else:
            # Too extreme — just force the duration (may freeze/skip frames)
            clip = clip.with_duration(audio_dur)
    else:
        clip = clip.with_duration(audio_dur)

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


def assemble_video(edl: list[dict], audio_path: Path, output_name: str = "final_video.mp4") -> Path:
    """
    Execute the EDL: load clips, apply transitions, overlay audio, render.

    Args:
        edl: The Edit Decision List (list of dicts from timeline_builder)
        audio_path: Path to the narration audio file
        output_name: Filename for the output video

    Returns:
        Path to the rendered video file.
    """
    print(f"[Video Assembler] Building video from {len(edl)} EDL entries...")

    # Sort EDL by audio_start to ensure correct ordering
    edl_sorted = sorted(edl, key=lambda e: e["audio_start"])

    # Load and trim all clips
    clips = []
    for i, entry in enumerate(edl_sorted):
        print(f"  [{i + 1}/{len(edl_sorted)}] Loading segment {entry['segment_id']}...")
        clip = _load_and_trim_clip(entry)

        # Apply crossfade-in if specified (first clip never fades in)
        if i > 0 and entry.get("transition") == "crossfade":
            fade_dur = entry.get("transition_duration", 0.3)
            clip = clip.with_effects([vfx.CrossFadeIn(fade_dur)])

        clips.append(clip)

    if not clips:
        raise RuntimeError("No clips to assemble — EDL is empty.")

    # Concatenate all clips
    print("[Video Assembler] Concatenating clips...")
    video = concatenate_videoclips(clips, method="compose")

    # Overlay narration audio
    print("[Video Assembler] Overlaying narration audio...")
    narration = AudioFileClip(str(audio_path))

    # If there's original clip audio, mix it low under the narration
    if video.audio is not None:
        # Lower original clip audio to 10% volume
        bg_audio = video.audio * 0.1
        mixed_audio = CompositeAudioClip([narration, bg_audio])
        video = video.with_audio(mixed_audio)
    else:
        video = video.with_audio(narration)

    # Render
    output_path = OUTPUT_DIR / output_name
    print(f"[Video Assembler] Rendering to {output_path}...")
    video.write_videofile(
        str(output_path),
        fps=OUTPUT_FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
    )

    # Clean up
    video.close()
    narration.close()
    for c in clips:
        c.close()

    print(f"[Video Assembler] Done! Output: {output_path}")
    return output_path
