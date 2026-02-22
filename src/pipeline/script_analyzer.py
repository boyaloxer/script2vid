"""
Stage 1 — Script Analysis

Takes a raw video script and uses an LLM to decompose it into visual segments.
Each segment includes the original text, visual description, mood, and search keywords.

For large scripts (10 000+ characters), the script is split into paragraph-group
chunks and each chunk is processed in a separate LLM call to avoid timeouts.
Segments are renumbered sequentially after merging.
"""

import time

from src.utils.llm import chat_json

# Max characters per LLM chunk — smaller chunks produce shorter JSON responses,
# preventing LLM output truncation that causes JSONDecodeError.
_CHUNK_CHARS = 5_000

SYSTEM_PROMPT = """\
You are a video essay editor planning what the viewer should SEE for each segment of a script.

For each segment, provide:
- "segment_id": sequential integer starting at 1
- "text": the exact script text for this segment (a sentence or small group of sentences)
- "visual_description": a short description of the ideal shot
- "mood": one or two words for emotional tone
- "search_keywords": 2-4 keyword phrases for STOCK FOOTAGE search (see rules below)
- "visual_type": one of "specific_footage", "illustrative", or "data_visual"
- "quote_type": one of "none", "direct_quote", "statistic", or "source_citation". \
  Most segments should be "none". Only use for actual quotes or key statistics.
- "quote_text": text to display (only if quote_type is NOT "none"), under 120 chars.
- "quote_attribution": who said it (only for quotes/citations).

CRITICAL RULES FOR search_keywords (this determines what footage appears in the video):

search_keywords are used to search a STOCK FOOTAGE library (like Pexels/Shutterstock). \
Stock footage does NOT contain specific branded products, named people, or historical events. \
You must translate specific references into GENERIC visual equivalents.

EXAMPLES OF GOOD TRANSLATIONS:
- Script mentions "NES controller" → search: "retro gaming controller", "vintage gamepad"
- Script mentions "Hiroshi Yamauchi" → search: "Japanese businessman office", "corporate executive"
- Script mentions "video game crash of 1983" → search: "empty store shelves", "economic decline"
- Script mentions "Famicom launch in Japan" → search: "product launch Japan", "electronics store"
- Script mentions "D-pad design" → search: "gaming controller close up", "game controller buttons"
- Script mentions "Lance Barr redesigned it" → search: "industrial designer working", "product design"
- Script mentions "it looked like a VCR" → search: "VCR vintage electronics", "retro technology"
- Script mentions "patent filed" → search: "patent document", "legal paperwork"

THE MAIN SUBJECT RULE: Identify the GENERIC visual category of the main subject. \
If the video is about a gaming controller, at least 40-50% of search_keywords should \
reference gaming/controllers generically: "gaming controller", "retro gaming", \
"video game controller close up", "person playing video games", "arcade gaming". \
Variety is important — use different angles and contexts of the same general subject.

VARIETY RULE: Adjacent segments should NOT have identical search_keywords. \
Vary the angle: "retro gaming controller" then "person playing video games" then \
"gaming controller buttons close up" — not the same phrase three times in a row.

Respond ONLY with a valid JSON array of segment objects. No extra text.\
"""


def _split_script_into_chunks(script_text: str) -> list[str]:
    """Split script into chunks at paragraph boundaries, each <= _CHUNK_CHARS."""
    paragraphs = script_text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para) + 2  # account for the \n\n separator
        if current and (current_len + para_len) > _CHUNK_CHARS:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


_MAX_RETRIES = 3


def _analyze_chunk(chunk_text: str, chunk_label: str = "") -> list[dict]:
    """Send a single chunk to the LLM and return parsed segments.
    
    Retries up to _MAX_RETRIES times on JSON parse errors (truncated output).
    """
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            segments = chat_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=chunk_text,
            )
        except Exception as e:
            last_error = e
            print(f"  [retry {attempt}/{_MAX_RETRIES}] {chunk_label} "
                  f"LLM call failed: {e}")
            if attempt < _MAX_RETRIES:
                time.sleep(5 * attempt)  # backoff: 5s, 10s
                continue
            raise ValueError(
                f"LLM failed after {_MAX_RETRIES} attempts for {chunk_label}: {last_error}"
            ) from last_error

        if not isinstance(segments, list) or len(segments) == 0:
            last_error = ValueError("LLM returned an invalid or empty segment list.")
            print(f"  [retry {attempt}/{_MAX_RETRIES}] {chunk_label} "
                  f"Invalid response, retrying...")
            if attempt < _MAX_RETRIES:
                time.sleep(5 * attempt)
                continue
            raise last_error

        required_keys = {"segment_id", "text", "visual_description", "mood", "search_keywords"}
        # Ensure new fields have defaults for backward compatibility
        for seg in segments:
            seg.setdefault("visual_type", "illustrative")
            seg.setdefault("visual_search", " ".join(seg.get("search_keywords", [])[:2]))
            seg.setdefault("context_label", None)
        for seg in segments:
            missing = required_keys - set(seg.keys())
            if missing:
                raise ValueError(
                    f"Segment {seg.get('segment_id', '?')} is missing keys: {missing}"
                )

        return segments

    # Should not reach here, but just in case
    raise ValueError(f"All retries exhausted for {chunk_label}")


def analyze_script(script_text: str) -> list[dict]:
    """
    Decompose a script into visual segments.

    For scripts over _CHUNK_CHARS, the text is split at paragraph boundaries
    and each chunk is processed in a separate LLM call. Segments are merged
    and renumbered sequentially.

    Returns a list of dicts, each with keys:
        segment_id, text, visual_description, mood, search_keywords
    """
    chunks = _split_script_into_chunks(script_text)

    if len(chunks) == 1:
        # Small script — single LLM call
        all_segments = _analyze_chunk(chunks[0], chunk_label="chunk 1/1")
    else:
        print(f"[Script Analyzer] Script is {len(script_text):,} chars "
              f"-> splitting into {len(chunks)} chunks...")
        all_segments: list[dict] = []
        for i, chunk in enumerate(chunks, 1):
            label = f"chunk {i}/{len(chunks)}"
            print(f"[Script Analyzer] {label} "
                  f"({len(chunk):,} chars)...")
            segs = _analyze_chunk(chunk, chunk_label=label)
            all_segments.extend(segs)
            # Small delay between LLM calls to be polite
            if i < len(chunks):
                time.sleep(2)

    # Renumber segment_ids sequentially across all chunks
    for idx, seg in enumerate(all_segments, 1):
        seg["segment_id"] = idx

    print(f"[Script Analyzer] Decomposed script into {len(all_segments)} segments.")
    return all_segments
