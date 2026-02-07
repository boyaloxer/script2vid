"""
Stage 1 — Script Analysis

Takes a raw video script and uses an LLM to decompose it into visual segments.
Each segment includes the original text, visual description, mood, and search keywords.
"""

from src.llm import chat_json

SYSTEM_PROMPT = """\
You are a video production assistant. Your job is to analyze a video script and \
break it into visual segments for a stock-footage-based video.

For each segment, provide:
- "segment_id": sequential integer starting at 1
- "text": the exact script text for this segment (a sentence or small group of sentences)
- "visual_description": a concise description of what the viewer should SEE on screen \
  while this text is being narrated (be specific and visual, not abstract)
- "mood": one or two words describing the emotional tone (e.g. "calm", "energetic", "dramatic")
- "search_keywords": a list of 2-4 short keyword phrases optimized for searching stock \
  footage (e.g. ["city skyline night", "urban lights aerial"])

Rules:
- Keep segments short — typically one or two sentences each.
- Every word of the original script must appear in exactly one segment (no omissions, no overlap).
- Visual descriptions should be concrete enough to find matching stock footage \
  (avoid vague terms like "concept of growth").
- Keywords should be diverse — don't repeat the same keyword across segments when possible.

Respond ONLY with a valid JSON array of segment objects. No extra text.\
"""


def analyze_script(script_text: str) -> list[dict]:
    """
    Decompose a script into visual segments.

    Returns a list of dicts, each with keys:
        segment_id, text, visual_description, mood, search_keywords
    """
    segments = chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=script_text,
    )

    # Validate basic structure
    if not isinstance(segments, list) or len(segments) == 0:
        raise ValueError("LLM returned an invalid or empty segment list.")

    required_keys = {"segment_id", "text", "visual_description", "mood", "search_keywords"}
    for seg in segments:
        missing = required_keys - set(seg.keys())
        if missing:
            raise ValueError(f"Segment {seg.get('segment_id', '?')} is missing keys: {missing}")

    print(f"[Script Analyzer] Decomposed script into {len(segments)} segments.")
    return segments
