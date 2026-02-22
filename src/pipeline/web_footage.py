"""
Web Footage Retrieval — PRIMARY footage source for video essays

Searches the web for REAL images and video clips that match each script
segment's visual research directive. The script analyzer produces a
specific "visual_search" query for each segment — this module executes
those searches across multiple sources.

Source priority:
  1. YouTube (via yt-dlp) — real event footage, documentary clips, b-roll
  2. Pixabay videos — free stock video (higher quality than Pexels for some queries)
  3. Pixabay/Google Images → Ken Burns animated video

Pexels is used separately as a FALLBACK only when this module finds nothing.

Each downloaded asset gets a JSON sidecar with metadata (source, query,
segment mapping, context label). Images are converted to video clips
using a Ken Burns pan/zoom effect via FFmpeg.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import src.config as _cfg


def _ffmpeg_path() -> str:
    return os.environ.get("IMAGEIO_FFMPEG_EXE") or shutil.which("ffmpeg") or "ffmpeg"


def _safe_print(msg: str):
    """Print with fallback encoding for Windows consoles that can't handle Unicode."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


# ─── YouTube via yt-dlp ──────────────────────────────────────────────

def search_youtube_clips(
    query: str,
    max_results: int = 5,
    max_duration: int = 60,
) -> list[dict]:
    """
    Search YouTube for short clips matching a query. Returns metadata only
    (no download yet). Uses yt-dlp's search functionality.
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": f"ytsearch{max_results}",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            entries = result.get("entries", []) if result else []

        clips = []
        for entry in entries:
            if not entry:
                continue
            duration = entry.get("duration") or 0
            if duration > max_duration:
                continue
            clips.append({
                "id": entry.get("id", ""),
                "title": entry.get("title", ""),
                "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                "duration": duration,
                "source": "youtube",
            })
        return clips

    except Exception as e:
        _safe_print(f"[WebFootage] YouTube search failed for '{query}': {e}")
        return []


def download_youtube_clip(
    video_url: str,
    dest_path: Path,
    max_duration: int = 30,
) -> Path | None:
    """
    Download a YouTube video clip. Limits to max_duration seconds.
    Returns the path to the downloaded file, or None on failure.
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/bestvideo+bestaudio/best",
        "outtmpl": str(dest_path.with_suffix(".%(ext)s")),
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        # Find the output file (yt-dlp may use different extensions)
        mp4 = dest_path.with_suffix(".mp4")
        if mp4.exists():
            return mp4
        for ext in [".webm", ".mkv", ".mp4"]:
            candidate = dest_path.with_suffix(ext)
            if candidate.exists():
                return candidate
        # Check exact path too
        if dest_path.exists():
            return dest_path

        return None

    except Exception as e:
        _safe_print(f"[WebFootage] YouTube download failed: {e}")
        return None


# ─── Pixabay ─────────────────────────────────────────────────────────

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_BASE = "https://pixabay.com/api"


def search_pixabay_videos(query: str, max_results: int = 5) -> list[dict]:
    """Search Pixabay for free stock videos."""
    if not PIXABAY_API_KEY:
        return []

    try:
        resp = requests.get(
            f"{PIXABAY_BASE}/videos/",
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": max_results,
                "safesearch": "true",
            },
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

        return [
            {
                "id": str(h.get("id", "")),
                "title": h.get("tags", ""),
                "url": h.get("videos", {}).get("medium", {}).get("url", ""),
                "duration": h.get("duration", 0),
                "source": "pixabay",
                "page_url": h.get("pageURL", ""),
            }
            for h in hits
            if h.get("videos", {}).get("medium", {}).get("url")
        ]

    except Exception as e:
        _safe_print(f"[WebFootage] Pixabay search failed for '{query}': {e}")
        return []


def search_pixabay_images(query: str, max_results: int = 5) -> list[dict]:
    """Search Pixabay for free stock images."""
    if not PIXABAY_API_KEY:
        return []

    try:
        resp = requests.get(
            f"{PIXABAY_BASE}/",
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": max_results,
                "image_type": "photo",
                "safesearch": "true",
                "min_width": 1280,
            },
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

        return [
            {
                "id": str(h.get("id", "")),
                "title": h.get("tags", ""),
                "url": h.get("largeImageURL", ""),
                "width": h.get("imageWidth", 0),
                "height": h.get("imageHeight", 0),
                "source": "pixabay_image",
                "page_url": h.get("pageURL", ""),
            }
            for h in hits
            if h.get("largeImageURL")
        ]

    except Exception as e:
        _safe_print(f"[WebFootage] Pixabay image search failed for '{query}': {e}")
        return []


# ─── Google Images (scrape, no API key needed) ───────────────────────

def search_google_images(query: str, max_results: int = 5) -> list[dict]:
    """
    Scrape Google Images for high-res photos. No API key needed.
    Returns image URLs with metadata.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=isch&tbs=isz:l"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        # Extract image URLs from the page
        image_urls = re.findall(
            r'"(https://[^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
            resp.text,
        )

        # Filter out thumbnails and Google's own assets
        filtered = [
            u for u in image_urls
            if "gstatic.com" not in u
            and "google.com" not in u
            and len(u) < 500
        ][:max_results]

        return [
            {
                "id": f"gimg_{i}",
                "title": query,
                "url": img_url,
                "source": "google_image",
            }
            for i, img_url in enumerate(filtered)
        ]

    except Exception as e:
        _safe_print(f"[WebFootage] Google Image search failed for '{query}': {e}")
        return []


# ─── Download helpers ─────────────────────────────────────────────────

def download_file(url: str, dest: Path, timeout: int = 60) -> Path | None:
    """Download a file from URL to local path."""
    try:
        resp = requests.get(
            url,
            stream=True,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; script2vid/1.0)"
            },
        )
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return dest if dest.exists() and dest.stat().st_size > 1000 else None

    except Exception as e:
        _safe_print(f"[WebFootage] Download failed ({url[:60]}...): {e}")
        if dest.exists():
            dest.unlink()
        return None


def image_to_video(
    image_path: Path,
    output_path: Path,
    duration: float = 6.0,
    width: int | None = None,
    height: int | None = None,
) -> Path | None:
    """Convert a static image to a video clip (scale + hold)."""
    w = width or _cfg.OUTPUT_WIDTH
    h = height or _cfg.OUTPUT_HEIGHT

    cmd = [
        _ffmpeg_path(), "-y",
        "-loop", "1", "-framerate", "1", "-i", str(image_path),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-t", str(duration),
        "-r", str(_cfg.OUTPUT_FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        str(output_path),
    ]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            _, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _safe_print("[WebFootage] Image-to-video timed out (30s) — skipping")
            return None

        if proc.returncode != 0:
            _safe_print(f"[WebFootage] Image-to-video failed: {stderr[-300:]}")
            return None

        if output_path.exists() and output_path.stat().st_size > _MIN_CLIP_BYTES:
            return output_path
        return None
    except Exception as e:
        _safe_print(f"[WebFootage] Image-to-video error: {e}")
        return None


_MIN_CLIP_BYTES = 1024  # 1 KB — anything smaller is corrupt


def _valid_clip(path: Path) -> bool:
    """Return True if path exists and is large enough to be a real video/image."""
    return path.exists() and path.stat().st_size >= _MIN_CLIP_BYTES


def _save_metadata(clip_path: Path, meta: dict):
    """Save a JSON sidecar alongside the clip file."""
    meta_path = clip_path.with_suffix(".json")
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ─── Main orchestrator ───────────────────────────────────────────────

def find_web_footage_for_segment(
    segment: dict,
    clips_dir: Path,
    used_ids: set | None = None,
) -> dict | None:
    """
    Find and download the best web footage for a single segment.

    Uses the visual_search field from the script analyzer (the primary query),
    falling back to search_keywords if visual_search isn't available.

    Source priority:
      1. YouTube clips (real footage — best for specific_footage type)
      2. Pixabay videos (free stock)
      3. Google/Pixabay images → Ken Burns video (for stills)

    Returns metadata dict for the downloaded clip, or None if nothing found.
    """
    if used_ids is None:
        used_ids = set()

    seg_id = segment["segment_id"]
    visual_type = segment.get("visual_type", "illustrative")
    primary_query = segment.get("visual_search", "")
    context_label = segment.get("context_label")

    if not primary_query:
        keywords = segment.get("search_keywords", [])
        primary_query = " ".join(keywords[:2])

    # Build query variants: primary + a fallback
    keywords = segment.get("search_keywords", [])
    fallback_query = " ".join(keywords[:2]) if keywords else primary_query

    # For specific footage, search YouTube first (real events, places, etc.)
    # For illustrative, YouTube is still good but images also work well
    video_queries = [primary_query]
    if fallback_query != primary_query:
        video_queries.append(fallback_query)

    image_queries = [primary_query, fallback_query]

    # ── Try YouTube first ──
    for query in video_queries:
        _safe_print(f"[WebFootage] Seg {seg_id} [{visual_type}]: YouTube '{query[:60]}'...")
        results = search_youtube_clips(query, max_results=5)

        for clip in results:
            clip_id = f"yt_{clip['id']}"
            if clip_id in used_ids:
                continue

            dest = clips_dir / f"seg{seg_id}_{clip_id}.mp4"
            if _valid_clip(dest):
                used_ids.add(clip_id)
                return _make_result(dest, "youtube", clip, segment)

            _safe_print(f"[WebFootage]   Downloading: {clip['title'][:60]}...")
            downloaded = download_youtube_clip(clip["url"], dest)
            if downloaded:
                used_ids.add(clip_id)
                _save_metadata(downloaded, {
                    "segment_id": seg_id,
                    "source": "youtube",
                    "video_id": clip["id"],
                    "title": clip["title"],
                    "query": query,
                    "visual_type": visual_type,
                    "context_label": context_label,
                })
                return _make_result(downloaded, "youtube", clip, segment)

    # ── Try Pixabay videos ──
    for query in video_queries:
        clean_q = query.replace(" b-roll", "").replace(" cinematic", "").replace(" footage", "")
        results = search_pixabay_videos(clean_q)
        for clip in results:
            clip_id = f"pb_{clip['id']}"
            if clip_id in used_ids:
                continue

            dest = clips_dir / f"seg{seg_id}_{clip_id}.mp4"
            if _valid_clip(dest):
                used_ids.add(clip_id)
                return _make_result(dest, "pixabay", clip, segment)

            _safe_print(f"[WebFootage]   Pixabay: {clip['title'][:60]}...")
            downloaded = download_file(clip["url"], dest)
            if downloaded:
                used_ids.add(clip_id)
                _save_metadata(downloaded, {
                    "segment_id": seg_id,
                    "source": "pixabay",
                    "clip_id": clip["id"],
                    "query": query,
                    "page_url": clip.get("page_url", ""),
                    "context_label": context_label,
                })
                return _make_result(downloaded, "pixabay", clip, segment)

    # ── Try images → Ken Burns ──
    for query in image_queries:
        images = search_pixabay_images(query)
        if not images:
            images = search_google_images(query)

        for img in images:
            img_id = f"img_{img['source']}_{img['id']}"
            if img_id in used_ids:
                continue

            ext = ".jpg"
            if ".png" in img["url"].lower():
                ext = ".png"
            elif ".webp" in img["url"].lower():
                ext = ".webp"

            img_dest = clips_dir / f"seg{seg_id}_{img_id}{ext}"
            vid_dest = clips_dir / f"seg{seg_id}_{img_id}.mp4"

            if _valid_clip(vid_dest):
                used_ids.add(img_id)
                return _make_result(vid_dest, img["source"], img, segment)

            _safe_print(f"[WebFootage]   Image: {img.get('title', query)[:50]}...")
            downloaded_img = download_file(img["url"], img_dest)
            if downloaded_img:
                _safe_print(f"[WebFootage]   Converting to Ken Burns video...")
                video = image_to_video(downloaded_img, vid_dest, duration=6.0)
                downloaded_img.unlink(missing_ok=True)
                if video:
                    used_ids.add(img_id)
                    _save_metadata(video, {
                        "segment_id": seg_id,
                        "source": img["source"],
                        "image_id": img["id"],
                        "query": query,
                        "original_url": img["url"],
                        "effect": "ken_burns",
                        "context_label": context_label,
                    })
                    return _make_result(video, img["source"], img, segment)

    _safe_print(f"[WebFootage] Seg {seg_id}: no web footage found")
    return None


def _make_result(path: Path, source: str, clip: dict, segment: dict) -> dict:
    return {
        "path": str(path),
        "source": source,
        "duration": clip.get("duration", 6),
        "title": clip.get("title", ""),
        "context_label": segment.get("context_label"),
        "visual_type": segment.get("visual_type", "illustrative"),
    }


def find_web_footage_for_segments(
    segments: list[dict],
    clips_dir: Path,
    skip_existing: bool = True,
) -> list[dict]:
    """
    PRIMARY footage finder — searches the web for real footage matching
    each segment's visual research directive.

    When skip_existing=True, only processes segments without footage.
    When skip_existing=False, processes ALL segments (web-first mode).

    Returns the enriched segment list.
    """
    used_ids = set()
    processed = 0
    total = 0

    for seg in segments:
        if skip_existing and seg.get("footage_path"):
            continue
        total += 1

        try:
            result = find_web_footage_for_segment(seg, clips_dir, used_ids)
            if result:
                seg["footage_path"] = result["path"]
                seg["footage_source"] = result.get("source", "web")
                seg["footage_duration"] = result.get("duration", 6)
                seg["context_label"] = result.get("context_label")
                processed += 1
        except Exception as e:
            _safe_print(f"[WebFootage] Seg {seg.get('segment_id', '?')}: error — {e}")

    _safe_print(f"[WebFootage] Found web footage for {processed}/{total} segments")
    return segments
