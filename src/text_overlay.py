"""
Text Overlay Generator — Pillow-based styled quote/citation cards

Generates transparent PNG overlays for three quote types:
  1. direct_quote  — dark card with accent line, quote text, attribution
  2. statistic     — large bold number with context text, no box
  3. source_citation — small pill-shaped badge in the lower-right corner

Each overlay is sized for the configured output resolution (default 1920x1080)
and designed to be composited onto video frames by FFmpeg's overlay filter.

Design language: clean business editorial (semi-transparent dark backgrounds,
modern sans-serif type, thin accent lines).
"""

import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.config import OUTPUT_WIDTH, OUTPUT_HEIGHT


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

def _find_system_font(preferred: list[str], bold: bool = False) -> str | None:
    """
    Try to locate a preferred font on the system.
    Returns the path to the first font found, or None.
    """
    # Common font directories by OS
    font_dirs = []
    if os.name == "nt":
        font_dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    else:
        font_dirs.extend([
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
        ])

    suffix = "bd" if bold else ""
    for name in preferred:
        for font_dir in font_dirs:
            if not font_dir.exists():
                continue
            # Try common naming patterns
            for pattern in [
                f"{name}{suffix}.ttf",
                f"{name}-{'Bold' if bold else 'Regular'}.ttf",
                f"{name}.ttf",
            ]:
                match = list(font_dir.rglob(pattern))
                if match:
                    return str(match[0])
    return None


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a clean sans-serif font, falling back gracefully."""
    preferred = ["Inter", "Segoe UI", "SegoeUI", "arial", "Arial",
                 "Helvetica", "DejaVuSans", "Liberation Sans"]
    path = _find_system_font(preferred, bold=bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass

    # Bold fallback: try regular font
    if bold:
        path = _find_system_font(preferred, bold=False)
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass

    # Last resort: Pillow's built-in bitmap font
    return ImageFont.load_default(size)


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

# Semi-transparent dark card background
_CARD_BG = (15, 15, 20, 200)        # near-black, ~78% opacity
_CARD_RADIUS = 12

# Accent line (left edge of direct_quote cards)
_ACCENT_COLOR = (70, 130, 255)       # calm blue — override via config later
_ACCENT_WIDTH = 5

# Text colors
_TEXT_PRIMARY = (255, 255, 255, 255)       # white
_TEXT_SECONDARY = (180, 180, 195, 230)     # light gray, slightly transparent
_TEXT_ACCENT = (70, 130, 255, 255)         # accent blue for numbers

# Source citation pill
_PILL_BG = (15, 15, 20, 170)        # slightly more transparent
_PILL_RADIUS = 16


# ---------------------------------------------------------------------------
# Direct Quote Card
# ---------------------------------------------------------------------------

def _render_direct_quote(
    quote_text: str,
    attribution: str | None = None,
) -> Image.Image:
    """
    Render a direct-quote card: dark rounded rectangle with an accent line
    on the left, quote text in white, attribution in gray below.

    Positioned in the lower-left area of the frame.

    Returns an RGBA Image at OUTPUT_WIDTH x OUTPUT_HEIGHT.
    """
    canvas = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    font_quote = _load_font(34)
    font_attr = _load_font(24)

    # Layout constants
    card_margin_x = 80
    card_margin_bottom = 120
    card_padding_x = 32
    card_padding_y = 24
    max_text_width = OUTPUT_WIDTH - 2 * card_margin_x - 2 * card_padding_x - _ACCENT_WIDTH - 16

    # Wrap text to fit card width
    avg_char_width = font_quote.getlength("M")
    chars_per_line = max(20, int(max_text_width / avg_char_width))
    wrapped = textwrap.fill(quote_text, width=chars_per_line)
    wrapped_lines = wrapped.split("\n")

    # Measure text dimensions
    line_height = font_quote.size + 8
    quote_block_h = line_height * len(wrapped_lines)

    attr_h = 0
    attr_text = ""
    if attribution:
        attr_text = f"-- {attribution}"
        attr_h = font_attr.size + 16  # gap + text

    # Card dimensions
    card_w = OUTPUT_WIDTH - 2 * card_margin_x
    card_h = card_padding_y + quote_block_h + attr_h + card_padding_y
    card_x = card_margin_x
    card_y = OUTPUT_HEIGHT - card_margin_bottom - card_h

    # Draw card background
    draw.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=_CARD_RADIUS,
        fill=_CARD_BG,
    )

    # Draw accent line on left edge
    accent_x = card_x + 12
    accent_y1 = card_y + card_padding_y
    accent_y2 = card_y + card_h - card_padding_y
    draw.rounded_rectangle(
        [accent_x, accent_y1, accent_x + _ACCENT_WIDTH, accent_y2],
        radius=2,
        fill=_ACCENT_COLOR + (255,),
    )

    # Draw quote text
    text_x = accent_x + _ACCENT_WIDTH + 16
    text_y = card_y + card_padding_y
    for line in wrapped_lines:
        draw.text((text_x, text_y), line, fill=_TEXT_PRIMARY, font=font_quote)
        text_y += line_height

    # Draw attribution
    if attr_text:
        text_y += 8  # small gap
        draw.text((text_x, text_y), attr_text, fill=_TEXT_SECONDARY, font=font_attr)

    return canvas


# ---------------------------------------------------------------------------
# Statistic Card
# ---------------------------------------------------------------------------

def _render_statistic(
    quote_text: str,
    attribution: str | None = None,
) -> Image.Image:
    """
    Render a statistic overlay: large bold number/figure centered on screen
    with smaller context text below. No card background — just text with a
    subtle drop shadow for readability over any footage.

    Returns an RGBA Image at OUTPUT_WIDTH x OUTPUT_HEIGHT.
    """
    canvas = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    font_big = _load_font(80, bold=True)
    font_context = _load_font(28)

    # Split quote_text into the "big number" and context
    # If it's short enough, use it all as the big number
    # Otherwise, first line = big number, rest = context
    parts = quote_text.split("\n", 1)
    big_text = parts[0].strip()
    context_text = parts[1].strip() if len(parts) > 1 else (attribution or "")

    # Measure big text
    big_bbox = draw.textbbox((0, 0), big_text, font=font_big)
    big_w = big_bbox[2] - big_bbox[0]
    big_h = big_bbox[3] - big_bbox[1]

    # Center position (slightly above center for visual balance)
    big_x = (OUTPUT_WIDTH - big_w) // 2
    big_y = (OUTPUT_HEIGHT // 2) - big_h - 20

    # Draw a subtle backing rectangle behind the text for readability
    pad_x, pad_y = 40, 24
    backing_rect = [
        big_x - pad_x,
        big_y - pad_y,
        big_x + big_w + pad_x,
        big_y + big_h + pad_y + (60 if context_text else 0) + (40 if context_text else 0),
    ]
    draw.rounded_rectangle(backing_rect, radius=16, fill=(0, 0, 0, 160))

    # Draw big number with shadow
    shadow_offset = 2
    draw.text((big_x + shadow_offset, big_y + shadow_offset), big_text,
              fill=(0, 0, 0, 120), font=font_big)
    draw.text((big_x, big_y), big_text, fill=_TEXT_PRIMARY, font=font_big)

    # Draw context text below
    if context_text:
        ctx_bbox = draw.textbbox((0, 0), context_text, font=font_context)
        ctx_w = ctx_bbox[2] - ctx_bbox[0]
        ctx_x = (OUTPUT_WIDTH - ctx_w) // 2
        ctx_y = big_y + big_h + 20
        draw.text((ctx_x, ctx_y), context_text, fill=_TEXT_SECONDARY, font=font_context)

    return canvas


# ---------------------------------------------------------------------------
# Source Citation Pill
# ---------------------------------------------------------------------------

def _render_source_citation(
    quote_text: str,
    attribution: str | None = None,
) -> Image.Image:
    """
    Render a source citation as a small pill-shaped badge in the
    lower-right corner. Minimal footprint — doesn't compete with the video.

    Returns an RGBA Image at OUTPUT_WIDTH x OUTPUT_HEIGHT.
    """
    canvas = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    font = _load_font(22)

    display_text = quote_text
    if attribution and attribution not in quote_text:
        display_text = f"{attribution}: {quote_text}"

    # Truncate if too long
    if len(display_text) > 80:
        display_text = display_text[:77] + "..."

    # Measure text
    text_bbox = draw.textbbox((0, 0), display_text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    # Pill dimensions
    pill_pad_x = 18
    pill_pad_y = 10
    pill_w = text_w + 2 * pill_pad_x
    pill_h = text_h + 2 * pill_pad_y

    # Position: lower-right corner with margin
    pill_x = OUTPUT_WIDTH - pill_w - 40
    pill_y = OUTPUT_HEIGHT - pill_h - 40

    # Draw pill background
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=_PILL_RADIUS,
        fill=_PILL_BG,
    )

    # Draw text
    draw.text(
        (pill_x + pill_pad_x, pill_y + pill_pad_y),
        display_text,
        fill=_TEXT_SECONDARY,
        font=font,
    )

    return canvas


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_overlay(
    quote_type: str,
    quote_text: str,
    attribution: str | None = None,
    output_path: Path | None = None,
) -> Image.Image | None:
    """
    Generate a styled text overlay PNG based on the quote type.

    Args:
        quote_type: One of "direct_quote", "statistic", "source_citation".
                    Returns None for "none" or unrecognized types.
        quote_text: The text to display.
        attribution: Who said it / where it came from (optional).
        output_path: If provided, saves the PNG to this path.

    Returns:
        An RGBA Pillow Image, or None if quote_type is "none".
    """
    if not quote_text or quote_type == "none":
        return None

    renderers = {
        "direct_quote": _render_direct_quote,
        "statistic": _render_statistic,
        "source_citation": _render_source_citation,
    }

    renderer = renderers.get(quote_type)
    if renderer is None:
        return None

    img = renderer(quote_text, attribution)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "PNG")

    return img


def generate_overlays_for_segments(
    segments: list[dict],
    overlays_dir: Path,
) -> list[dict]:
    """
    Generate overlay PNGs for all segments that have quotes/citations.

    Mutates each segment in-place by adding:
        - "overlay_path": path to the generated PNG (or None)

    Returns the enriched segment list.
    """
    count = 0
    for seg in segments:
        quote_type = seg.get("quote_type", "none")
        quote_text = seg.get("quote_text")

        if quote_type == "none" or not quote_text:
            seg["overlay_path"] = None
            continue

        seg_id = seg["segment_id"]
        out_path = overlays_dir / f"overlay_seg{seg_id}_{quote_type}.png"

        if out_path.exists():
            print(f"[Overlay] Segment {seg_id}: using cached {out_path.name}")
            seg["overlay_path"] = str(out_path)
            count += 1
            continue

        attribution = seg.get("quote_attribution")

        print(f"[Overlay] Segment {seg_id}: generating {quote_type} overlay...")
        generate_overlay(
            quote_type=quote_type,
            quote_text=quote_text,
            attribution=attribution,
            output_path=out_path,
        )
        seg["overlay_path"] = str(out_path)
        count += 1

    print(f"[Overlay] Generated {count} text overlays.")
    return segments
