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
- "quote_type": one of "none", "direct_quote", "statistic", or "source_citation". \
  Use "direct_quote" when someone is being quoted or paraphrased (e.g. 'analysts said...', \
  'according to X...'). Use "statistic" when a striking number or data point is the focus \
  (e.g. '$2.6 billion in liquidations', 'dropped 46%'). Use "source_citation" when a \
  specific source or report is named without a direct quote (e.g. 'a report published by...', \
  'data from Glassnode showed...'). Use "none" for all other segments (narration, \
  transitions, analysis, etc.). Most segments should be "none" — only mark segments where \
  an on-screen text overlay would genuinely add value.
- "quote_text": the exact text to display on screen (only if quote_type is NOT "none"). \
  For "direct_quote": the quoted words or paraphrased claim, kept concise (under 120 chars). \
  For "statistic": the key number and brief context, e.g. "$2.6B liquidated in 24 hours". \
  For "source_citation": the source name and date, e.g. "Fortune, Feb 6 2026". \
  Omit this field or set to null when quote_type is "none".
- "quote_attribution": who said it or where it came from (only for "direct_quote" and \
  "source_citation", e.g. "Deutsche Bank analysts", "Bitwise CIO Matt Hougan"). \
  Omit or set to null when not applicable.

Rules:
- Keep segments short — typically one or two sentences each.
- Every word of the original script must appear in exactly one segment (no omissions, no overlap).
- Visual descriptions should be concrete enough to find matching stock footage \
  (avoid vague terms like "concept of growth").
- Keywords should be diverse — don't repeat the same keyword across segments when possible.
- Be selective with quote_type — only 10-20% of segments should have a non-"none" value. \
  Overusing text overlays makes the video cluttered. Reserve them for impactful moments.

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
