"""
Stage 2 — Footage Retrieval & Selection

Queries the Pexels Video API for each script segment, scores results,
selects the best clip, and downloads it locally.

Includes a sliding-window rate limiter to stay within Pexels' 200 req/hour
limit on long-form videos (80–150+ segments).
"""

import time
from pathlib import Path
import requests

import src.config as _cfg
from src.config import PEXELS_API_KEY, PEXELS_BASE_URL
from src.utils.rate_limiter import RateLimiter

# Shared rate limiter for all Pexels API calls in this module
_pexels_limiter = RateLimiter(
    max_requests=200,
    window_seconds=3600,
    headroom=20,
    name="Pexels",
)


def _pexels_headers() -> dict:
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY is not set. Add it to your .env file.")
    return {"Authorization": PEXELS_API_KEY}


def _detect_orientation() -> str:
    """Detect the desired orientation from the current output resolution."""
    # Access via module reference so --vertical runtime override is picked up
    if _cfg.OUTPUT_HEIGHT > _cfg.OUTPUT_WIDTH:
        return "portrait"
    elif _cfg.OUTPUT_WIDTH == _cfg.OUTPUT_HEIGHT:
        return "square"
    return "landscape"


def search_videos(query: str, per_page: int = 15, orientation: str | None = None) -> list[dict]:
    """
    Search Pexels for videos matching a query.
    Returns the raw list of video objects from the API response.
    Automatically respects the Pexels rate limit (200 req/hour).

    Orientation is auto-detected from the output resolution if not specified.
    """
    if orientation is None:
        orientation = _detect_orientation()

    _pexels_limiter.wait_if_needed()

    params = {
        "query": query,
        "per_page": per_page,
        "orientation": orientation,
        "size": "large",  # prefer HD/4K sources
    }
    max_retries = 5
    for attempt in range(max_retries):
        resp = requests.get(
            f"{PEXELS_BASE_URL}/search",
            headers=_pexels_headers(),
            params=params,
            timeout=30,
        )
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"[Footage Finder]   Rate limited — waiting {wait}s before retry...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        _pexels_limiter.record()
        return resp.json().get("videos", [])

    resp.raise_for_status()
    return []


def _pick_best_file(video: dict, min_height: int = 720) -> dict | None:
    """
    From a Pexels video object, pick the best-quality MP4 file.
    Prefers HD (1080p+) and falls back to the largest available.
    """
    candidates = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4"
    ]
    if not candidates:
        return None

    # Sort by height descending — pick the largest that's at least min_height
    candidates.sort(key=lambda f: f.get("height", 0), reverse=True)
    for c in candidates:
        if c.get("height", 0) >= min_height:
            return c
    # Fallback to the largest available
    return candidates[0]


def _score_video(video: dict, keywords: list[str]) -> float:
    """
    Simple relevance score: count how many of our keywords appear in the
    video's URL slug or tags. Higher is better.
    """
    # Pexels doesn't return explicit tags, but the video URL contains a slug
    url_slug = video.get("url", "").lower()
    score = 0.0
    for kw in keywords:
        for word in kw.lower().split():
            if word in url_slug:
                score += 1.0
    # Slight bonus for longer duration (more flexibility for trimming)
    duration = video.get("duration", 0)
    if duration >= 5:
        score += 0.5
    if duration >= 10:
        score += 0.5
    return score


def download_clip(url: str, dest: Path) -> Path:
    """
    Download a video file from a URL to a local path.
    """
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest


def find_footage_for_segments(
    segments: list[dict],
    clips_dir: Path,
    used_video_ids: set | None = None,
) -> list[dict]:
    """
    For each segment, search Pexels, pick the best clip, and download it.

    Mutates each segment dict in-place by adding:
        - "footage_path": local Path to the downloaded MP4
        - "footage_duration": duration of the source clip in seconds
        - "pexels_video_id": the Pexels video ID (for attribution)
        - "pexels_video_url": link to the video on Pexels
        - "pexels_videographer": name of the videographer
        - "pexels_videographer_url": link to their Pexels profile

    Also returns the enriched segment list.
    """
    if used_video_ids is None:
        used_video_ids = set()

    for seg in segments:
        seg_id = seg["segment_id"]

        # Check if a clip already exists on disk for this segment
        existing = list(clips_dir.glob(f"seg{seg_id}_*.mp4"))
        if existing:
            clip = existing[0]
            # Extract Pexels video ID from filename: seg{id}_{pexels_id}.mp4
            try:
                pexels_id = int(clip.stem.split("_", 1)[1])
            except (ValueError, IndexError):
                pexels_id = None
            print(f"[Footage Finder] Segment {seg_id}: using cached clip {clip.name}")
            seg["footage_path"] = str(clip)
            seg["footage_duration"] = seg.get("footage_duration", 10)
            seg["pexels_video_id"] = pexels_id
            seg["pexels_video_url"] = seg.get("pexels_video_url", "")
            seg["pexels_videographer"] = seg.get("pexels_videographer", "Unknown")
            seg["pexels_videographer_url"] = seg.get("pexels_videographer_url", "")
            if pexels_id:
                used_video_ids.add(pexels_id)
            continue

        keywords = seg["search_keywords"]
        query = " ".join(keywords[:2])  # combine top 2 keyword phrases

        print(f"[Footage Finder] Segment {seg_id}: searching \"{query}\"...")
        videos = search_videos(query)

        if not videos:
            # Retry with a broader single keyword
            query = keywords[0]
            print(f"[Footage Finder]   No results. Retrying with \"{query}\"...")
            videos = search_videos(query)

        if not videos:
            print(f"[Footage Finder]   WARNING: No footage found for segment {seg['segment_id']}.")
            seg["footage_path"] = None
            seg["footage_duration"] = 0
            seg["pexels_video_id"] = None
            seg["pexels_video_url"] = None
            seg["pexels_videographer"] = None
            seg["pexels_videographer_url"] = None
            continue

        # Score and sort, skipping already-used videos for variety
        scored = []
        for v in videos:
            vid = v.get("id")
            if vid in used_video_ids:
                continue
            scored.append((v, _score_video(v, keywords)))
        scored.sort(key=lambda x: x[1], reverse=True)

        # If all videos were used, allow repeats
        if not scored:
            scored = [(v, _score_video(v, keywords)) for v in videos]
            scored.sort(key=lambda x: x[1], reverse=True)

        best_video = scored[0][0]
        best_file = _pick_best_file(best_video)

        if not best_file:
            print(f"[Footage Finder]   WARNING: No suitable MP4 for segment {seg['segment_id']}.")
            seg["footage_path"] = None
            seg["footage_duration"] = 0
            seg["pexels_video_id"] = None
            seg["pexels_video_url"] = None
            seg["pexels_videographer"] = None
            seg["pexels_videographer_url"] = None
            continue

        # Download
        video_id = best_video["id"]
        dest = clips_dir / f"seg{seg['segment_id']}_{video_id}.mp4"

        if not dest.exists():
            print(f"[Footage Finder]   Downloading clip {video_id}...")
            download_clip(best_file["link"], dest)
        else:
            print(f"[Footage Finder]   Using cached clip {dest.name}")

        used_video_ids.add(video_id)
        seg["footage_path"] = str(dest)
        seg["footage_duration"] = best_video.get("duration", 0)
        seg["pexels_video_id"] = video_id
        seg["pexels_video_url"] = best_video.get("url", "")
        seg["pexels_videographer"] = best_video.get("user", {}).get("name", "Unknown")
        seg["pexels_videographer_url"] = best_video.get("user", {}).get("url", "")

    found = sum(1 for s in segments if s.get("footage_path"))
    print(
        f"[Footage Finder] Found footage for {found}/{len(segments)} segments. "
        f"({_pexels_limiter.requests_used} Pexels API calls used this hour)"
    )
    return segments
