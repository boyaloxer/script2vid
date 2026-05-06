"""
Visual relevance verification for footage candidates.

Pexels and other stock APIs return clips described only by their URL slug
or generic tags, which are unreliable signals for whether the actual visual
content matches the script. This module sends representative frames of each
candidate clip to a multimodal LLM (Gemini) and asks it to score how well
the footage matches the script segment.

Used as a re-ranker on top of the text-based relevance scorer in
``footage_finder.py``. When :data:`src.config.FOOTAGE_VISUAL_VERIFY` is
disabled or :data:`src.config.GEMINI_API_KEY` is missing, callers gracefully
fall back to text-only scoring.

Cost note: ~$0.001-0.004 per candidate with ``gemini-2.5-flash`` and 1-4
preview frames per clip. For a 60-segment video verifying the top 3 picks
each, total cost is roughly $0.20-0.80 per video.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

from src.config import (
    FOOTAGE_VISUAL_VERIFY,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)
from src.utils.retry import retry as _retry


_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Limit on how many preview frames we send per candidate. More frames give
# Gemini better context about motion but cost more tokens. 3 is a good balance.
_MAX_FRAMES_PER_CANDIDATE = 3


@dataclass
class VerificationResult:
    """Outcome of verifying a single footage candidate."""

    score: float  # 0.0 - 10.0; higher means more relevant
    description: str  # what Gemini saw in the clip
    raw_response: str  # raw model output for debugging


def is_available() -> bool:
    """Returns True when visual verification can actually run."""
    return bool(FOOTAGE_VISUAL_VERIFY and GEMINI_API_KEY)


def verify_candidate(
    frame_urls: list[str],
    segment_text: str,
    search_query: str = "",
) -> VerificationResult | None:
    """
    Ask Gemini whether a clip's preview frames match the segment.

    Args:
        frame_urls: HTTP URLs of preview frames from the clip (e.g. Pexels
            ``video_pictures`` or a single thumbnail). At most
            :data:`_MAX_FRAMES_PER_CANDIDATE` frames are sent.
        segment_text: The script text this clip would visualise.
        search_query: The search query that surfaced the clip (helps the
            model understand intent, optional).

    Returns:
        :class:`VerificationResult` on success, or ``None`` when the API call
        fails or visual verification is disabled. Callers should treat
        ``None`` as "no opinion" and fall back to text-based scoring.
    """
    if not is_available():
        return None

    if not frame_urls:
        return None

    frames = frame_urls[:_MAX_FRAMES_PER_CANDIDATE]

    image_parts = []
    for url in frames:
        try:
            img_bytes = _fetch_image(url)
        except requests.RequestException:
            continue
        if not img_bytes:
            continue
        import base64
        image_parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(img_bytes).decode("ascii"),
            }
        })

    if not image_parts:
        return None

    prompt = _build_prompt(segment_text, search_query, len(image_parts))

    try:
        raw = _call_gemini(prompt, image_parts)
    except requests.RequestException as e:
        print(f"[Verifier] Gemini API error: {e}")
        return None

    score, description = _parse_response(raw)
    return VerificationResult(score=score, description=description, raw_response=raw)


# ---------------------------------------------------------------------------
#  Internals
# ---------------------------------------------------------------------------


@_retry(max_attempts=2, base_delay=2.0, max_delay=8.0,
        exceptions=(requests.RequestException,))
def _fetch_image(url: str) -> bytes:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.content


def _build_prompt(segment_text: str, search_query: str, n_frames: int) -> str:
    frame_word = "frame" if n_frames == 1 else f"{n_frames} frames"
    query_line = f'\nSearch query that surfaced this clip: "{search_query}"' if search_query else ""
    return f"""You are evaluating whether a stock footage clip is visually relevant to a narration segment.

Narration segment: "{segment_text}"
{query_line}

You are looking at {frame_word} from the candidate clip.

Score 0-10 how well the visual content of these frames matches what the narration is talking about. Use this rubric:
- 9-10: Directly depicts the subject (e.g. narration about NES controllers, frame shows a gaming controller close up).
- 7-8: Strongly evokes the subject (e.g. narration about brain function, frame shows a brain scan).
- 5-6: Tangentially related but watchable (e.g. narration about anxiety, frame shows a worried person).
- 3-4: Generic stock footage that vaguely fits the mood but not the topic.
- 0-2: Wrong subject entirely or visually unhelpful.

Respond with a SINGLE LINE of strict JSON, nothing else:
{{"score": <number 0-10>, "shows": "<6-12 word description of what the frames actually depict>"}}"""


def _call_gemini(prompt: str, image_parts: list[dict]) -> str:
    url = f"{_GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}] + image_parts,
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 200,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(
        url,
        params={"key": GEMINI_API_KEY},
        json=body,
        timeout=60,
    )
    if not resp.ok:
        raise requests.HTTPError(
            f"Gemini {resp.status_code}: {resp.text[:300]}",
            response=resp,
        )
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(raw: str) -> tuple[float, str]:
    """Extract (score, description) from a Gemini reply."""
    if not raw:
        return 0.0, ""
    match = _JSON_RE.search(raw)
    if not match:
        return 0.0, raw[:80]
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 0.0, raw[:80]
    raw_score = data.get("score", 0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(10.0, score))
    description = str(data.get("shows", "")).strip()[:120]
    return score, description
