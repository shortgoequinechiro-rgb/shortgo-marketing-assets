"""
Short Go post composer.

Renders a finished 1080x1080 Instagram/Facebook post by overlaying branded
typography onto a background image from photo-library/.

Brand identity (locked 2026-05-14):
- Headline: Playfair Display Black (editorial serif)
- Body: Inter SemiBold/Medium (clean sans)
- Cornermark top-left, service tags top-right
- Big phone in gold serif at bottom
- Web URL + CTA in tracked uppercase sans at very bottom

Templates supported:
- 'educational' — hook + 3 symptom bullets + value line + contact block
- 'cta'         — booking-area announcement, big date/location, contact block
- 'quote'       — single big serif quote, attribution, contact block

Usage:
    from compose_post import compose_post
    compose_post(
        background_path=Path("photo-library/001-arena-sunrise-empty.png"),
        template="educational",
        hook=["IT'S NOT", "THE BIT."],
        body=["He's stopping crooked.",
              "Drifting on the second barrel.",
              "Off behind through the turn."],
        explainer="The body is compensating. We come to your barn and fix it.",
        output_path=Path("out.png"),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------

# Font paths — caller can override but these are the defaults.
# Fonts ship inside lib/fonts/ so the renderer is self-contained.
_LIB_DIR = Path(__file__).resolve().parent
DEFAULT_PLAYFAIR = _LIB_DIR / "fonts" / "PlayfairDisplay.ttf"
DEFAULT_INTER = _LIB_DIR / "fonts" / "Inter.ttf"

# Locked brand colors (RGBA)
CREAM = (252, 245, 230, 255)
CREAM_DIM = (235, 220, 200, 220)
CREAM_FAINT = (235, 220, 200, 180)
GOLD = (220, 175, 95, 235)
SHADOW = (0, 0, 0, 150)
SHADOW_LIGHT = (0, 0, 0, 120)

# Locked brand strings (override per-call only if a campaign requires it)
DEFAULT_PHONE = "(406) 799-3369"
DEFAULT_WEB = "SHORTGOCHIRO.COM"
DEFAULT_CTA = "DM \"BOOK\" TO RESERVE"
DEFAULT_CORNERMARK = "SHORT GO EQUINE CHIROPRACTIC"
DEFAULT_CORNERMARK_SUB = "DFW · NORTH TEXAS · MONTANA"
DEFAULT_SERVICE_TAGS = ("MOBILE · cAVCA-CERTIFIED", "WE COME TO YOUR BARN")

CANVAS_SIZE = 1080
MARGIN = 50


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def _font(path: Path, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    """Load a variable font at a given size and (if supported) weight axis."""
    f = ImageFont.truetype(str(path), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            # Non-variable font or unsupported axis — fall back to plain weight
            pass
    return f


def _text_width(text: str, font: ImageFont.FreeTypeFont, tracking: int = 0) -> int:
    """Width of text in pixels, including per-character tracking gaps."""
    if not text:
        return 0
    total = 0
    for ch in text:
        bbox = font.getbbox(ch)
        total += (bbox[2] - bbox[0]) + tracking
    return total - tracking


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    tracking: int = 0,
    shadow: bool = False,
) -> None:
    """Draw text with explicit per-character spacing (for letter-spacing control)."""
    x, y = xy
    if shadow:
        sx = x
        for ch in text:
            draw.text((sx + 2, y + 2), ch, font=font, fill=SHADOW)
            bbox = font.getbbox(ch)
            sx += (bbox[2] - bbox[0]) + tracking
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        bbox = font.getbbox(ch)
        x += (bbox[2] - bbox[0]) + tracking


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    canvas_w: int = CANVAS_SIZE,
    tracking: int = 0,
    shadow: bool = True,
) -> None:
    """Center text horizontally at the given y, with optional drop shadow."""
    tw = _text_width(text, font, tracking)
    x = (canvas_w - tw) // 2
    _draw_tracked(draw, (x, y), text, font, fill, tracking=tracking, shadow=shadow)


def _wrap_to_width(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    tracking: int = 0,
) -> list[str]:
    """Greedy word-wrap so no line exceeds max_width pixels.

    Falls back to character-wrap for any single word that's still too wide.
    """
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = w if not cur else f"{cur} {w}"
        if _text_width(candidate, font, tracking) <= max_width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            # Hard-wrap a single oversized word
            if _text_width(w, font, tracking) > max_width:
                chunk = ""
                for ch in w:
                    test = chunk + ch
                    if _text_width(test, font, tracking) > max_width and chunk:
                        lines.append(chunk)
                        chunk = ch
                    else:
                        chunk = test
                cur = chunk
            else:
                cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Layout pieces
# ---------------------------------------------------------------------------

def _prepare_background(background_path: Path) -> Image.Image:
    """Load background, center-crop to square, resize to 1080, sharpen."""
    src = Image.open(background_path).convert("RGB")
    w, h = src.size
    side = min(w, h)
    cropped = src.crop(
        ((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)
    )
    resized = cropped.resize((CANVAS_SIZE, CANVAS_SIZE), Image.LANCZOS)
    return resized.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=2))


def _apply_bottom_gradient(canvas: Image.Image, start_pct: float = 0.42, peak_alpha: int = 240) -> Image.Image:
    """Composite a quadratic dark gradient on the bottom portion for text legibility."""
    W, H = canvas.size
    gradient = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(gradient)
    band_start = int(H * start_pct)
    for y in range(band_start, H):
        p = (y - band_start) / (H - band_start)
        alpha = int(min(peak_alpha, p * p * (peak_alpha + 40)))
        gdraw.line([(0, y), (W, y)], fill=(15, 10, 8, alpha))
    return Image.alpha_composite(canvas.convert("RGBA"), gradient)


def _apply_top_gradient(canvas: Image.Image, end_pct: float = 0.18, peak_alpha: int = 165) -> Image.Image:
    """Soft dark gradient along the top edge so cornermark + service tags stay legible
    over bright backgrounds (sun-beams, sky, etc.)."""
    W, H = canvas.size
    gradient = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(gradient)
    band_end = int(H * end_pct)
    for y in range(0, band_end):
        # Alpha highest at top, eases out toward band_end (quadratic falloff)
        p = 1 - (y / band_end)
        alpha = int(p * p * peak_alpha)
        gdraw.line([(0, y), (W, y)], fill=(15, 10, 8, alpha))
    return Image.alpha_composite(canvas.convert("RGBA"), gradient)


def _draw_cornermark(
    draw: ImageDraw.ImageDraw,
    fonts: dict,
    cornermark: str,
    cornermark_sub: str,
) -> None:
    """Top-left: tracked uppercase wordmark + divider + service area sub-line.
    Drop shadow ensures legibility even where the top gradient is mild."""
    mark_font = fonts["mark"]
    sub_font = fonts["mark_sub"]
    _draw_tracked(draw, (MARGIN, MARGIN), cornermark, mark_font, CREAM, tracking=3, shadow=True)
    line_x_end = MARGIN + _text_width(cornermark, mark_font, tracking=3)
    # Divider sits just below the wordmark baseline; shadow first then bright line
    draw.line([(MARGIN + 1, MARGIN + 38), (line_x_end + 1, MARGIN + 38)], fill=SHADOW_LIGHT, width=1)
    draw.line([(MARGIN, MARGIN + 37), (line_x_end, MARGIN + 37)], fill=CREAM_DIM, width=1)
    _draw_tracked(draw, (MARGIN, MARGIN + 48), cornermark_sub, sub_font, CREAM, tracking=2, shadow=True)


def _draw_service_tags(
    draw: ImageDraw.ImageDraw,
    fonts: dict,
    tags: Sequence[str],
) -> None:
    """Top-right: short service descriptors stacked right-aligned. Shadowed for legibility.

    Auto-shrinks the font size if the widest tag would clip the right margin.
    Tracking is reduced to 1 to recover ~5–10 px in addition to size scaling.
    """
    # Safe right-side width must equal cornermark column on the left (MARGIN)
    safe_w = CANVAS_SIZE - (2 * MARGIN)
    tracking = 1
    tag_font = fonts["tag"]
    # Find size that fits the widest tag
    max_w = max((_text_width(t, tag_font, tracking=tracking) for t in tags), default=0)
    size = tag_font.size if hasattr(tag_font, "size") else 15
    while max_w > safe_w and size > 10:
        size -= 1
        tag_font = _font(_LIB_DIR / "fonts" / "Inter.ttf", size, 600)
        max_w = max((_text_width(t, tag_font, tracking=tracking) for t in tags), default=0)

    y = MARGIN
    for i, tag in enumerate(tags):
        tw = _text_width(tag, tag_font, tracking=tracking)
        x = CANVAS_SIZE - MARGIN - tw
        fill = CREAM if i == 0 else CREAM_DIM
        _draw_tracked(draw, (x, y), tag, tag_font, fill, tracking=tracking, shadow=True)
        y += size + 7


def _draw_contact_block(
    draw: ImageDraw.ImageDraw,
    fonts: dict,
    phone: str,
    web: str,
    cta: str,
) -> None:
    """Bottom: big gold phone in serif, then web · CTA in tracked sans."""
    phone_font = fonts["phone"]
    web_font = fonts["web"]
    _draw_centered(draw, CANVAS_SIZE - 130, phone, phone_font, GOLD, tracking=1, shadow=True)
    combined = f"{web}  ·  {cta}"
    _draw_centered(draw, CANVAS_SIZE - 55, combined, web_font, CREAM, tracking=2, shadow=True)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

@dataclass
class PostSpec:
    """One post's content + chosen background + template."""
    background_path: Path
    template: Literal["educational", "cta", "quote"]
    hook: list[str]                  # 1–3 lines
    body: list[str] = field(default_factory=list)
    explainer: str | None = None
    phone: str = DEFAULT_PHONE
    web: str = DEFAULT_WEB
    cta: str = DEFAULT_CTA
    cornermark: str = DEFAULT_CORNERMARK
    cornermark_sub: str = DEFAULT_CORNERMARK_SUB
    service_tags: Sequence[str] = DEFAULT_SERVICE_TAGS
    playfair_path: Path = DEFAULT_PLAYFAIR
    inter_path: Path = DEFAULT_INTER


def compose_post(spec: PostSpec, output_path: Path) -> Path:
    """Render a finished 1080x1080 IG/FB post. Returns the saved path."""

    # 1. Background + dual gradients (top + bottom) for guaranteed text legibility
    img = _prepare_background(spec.background_path)
    canvas = Image.new("RGBA", img.size)
    canvas.paste(img, (0, 0))
    canvas = _apply_top_gradient(canvas, end_pct=0.20, peak_alpha=170)
    canvas = _apply_bottom_gradient(canvas, start_pct=0.40, peak_alpha=235)
    draw = ImageDraw.Draw(canvas)

    # 2. Load fonts at standard sizes
    # Cornermark / service tags bumped slightly so they hold up across all backgrounds.
    fonts = {
        "mark":     _font(spec.inter_path,    23, 900),    # heavier weight so cornermark reads on every background
        "mark_sub": _font(spec.inter_path,    16, 700),    # bolder + slightly larger subtitle
        "tag":      _font(spec.inter_path,    16, 800),    # bolder service tags top-right
        "hook_big": _font(spec.playfair_path, 96, 900),
        "hook_med": _font(spec.playfair_path, 72, 900),
        "body":     _font(spec.inter_path,    26, 700),    # bumped to SemiBold/Bold for legibility
        "explainer":_font(spec.inter_path,    24, 700),    # bold + larger so small text reads cleanly
        "phone":    _font(spec.playfair_path, 56, 900),
        "web":      _font(spec.inter_path,    24, 600),
    }

    # 3. Persistent UI: cornermark, service tags, contact block
    _draw_cornermark(draw, fonts, spec.cornermark, spec.cornermark_sub)
    _draw_service_tags(draw, fonts, spec.service_tags)
    _draw_contact_block(draw, fonts, spec.phone, spec.web, spec.cta)

    # 4. Template-specific middle content
    if spec.template == "educational":
        _layout_educational(draw, fonts, spec)
    elif spec.template == "cta":
        _layout_cta(draw, fonts, spec)
    elif spec.template == "quote":
        _layout_quote(draw, fonts, spec)
    else:
        raise ValueError(f"Unknown template: {spec.template}")

    # 5. Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# Template layouts
# ---------------------------------------------------------------------------

def _layout_educational(draw: ImageDraw.ImageDraw, fonts: dict, spec: PostSpec) -> None:
    """
    Educational post: hook (1–2 lines) + 3 symptom bullets + one-line explainer.
    Used for "It's not the bit." / "3 signs your horse..." angles.
    """
    # Pre-wrap hook lines so each fits the safe width
    hook_safe_w = CANVAS_SIZE - (2 * MARGIN) - 30
    hook_font = fonts["hook_big"] if len(spec.hook) <= 2 else fonts["hook_med"]
    wrapped_hook: list[str] = []
    for line in spec.hook:
        wrapped_hook.extend(_wrap_to_width(line, hook_font, hook_safe_w, tracking=1))
    # If wrapping ballooned the line count, fall back to smaller hook font
    if len(wrapped_hook) > 2 and hook_font is fonts["hook_big"]:
        hook_font = fonts["hook_med"]
        wrapped_hook = []
        for line in spec.hook:
            wrapped_hook.extend(_wrap_to_width(line, hook_font, hook_safe_w, tracking=1))
    line_h = 110 if hook_font is fonts["hook_big"] else 82

    total_hook_h = line_h * len(wrapped_hook)
    hook_start_y = 540 - (total_hook_h // 2) + (line_h // 2)

    for i, line in enumerate(wrapped_hook):
        _draw_centered(draw, hook_start_y + i * line_h, line, hook_font, CREAM, tracking=1)

    # Divider under hook
    div_y = hook_start_y + total_hook_h + 14
    draw.line([(CANVAS_SIZE // 2 - 60, div_y), (CANVAS_SIZE // 2 + 60, div_y)],
              fill=CREAM_DIM, width=2)

    # Body bullets (wrap each to safe width). Reserve room for the phone
    # block at the bottom — phone is centered at y = CANVAS_SIZE - 130, so
    # body content must stop above y ~ 870.
    body_safe_w = CANVAS_SIZE - (2 * MARGIN) - 30
    bottom_limit = CANVAS_SIZE - 200  # 880, leaves clearance for phone + web/CTA

    # Pre-compute total height needed so we can shrink font if it would overflow.
    body_font = fonts["body"]
    explainer_font = fonts["explainer"]

    def _measure(b_font, e_font, line_h, gap, exp_line_h):
        total = 0
        for line in spec.body:
            wrapped_lines = _wrap_to_width(line, b_font, body_safe_w)
            total += line_h * len(wrapped_lines) + gap
        if spec.explainer:
            total += 8
            total += exp_line_h * len(_wrap_to_width(spec.explainer, e_font, body_safe_w))
        return total

    line_h, gap, exp_line_h = 34, 4, 32
    available = bottom_limit - (div_y + 30)
    needed = _measure(body_font, explainer_font, line_h, gap, exp_line_h)
    if needed > available:
        # Drop a size and tighten spacing
        body_font = _font(spec.inter_path, 22, 700)
        explainer_font = _font(spec.inter_path, 21, 700)
        line_h, gap, exp_line_h = 30, 2, 28

    sy = div_y + 30
    for line in spec.body:
        for wrapped in _wrap_to_width(line, body_font, body_safe_w):
            if sy > bottom_limit - line_h:
                break
            _draw_centered(draw, sy, wrapped, body_font, CREAM, tracking=0)
            sy += line_h
        sy += gap

    if spec.explainer and sy + exp_line_h < bottom_limit:
        sy += 8
        for wrapped in _wrap_to_width(spec.explainer, explainer_font, body_safe_w):
            if sy > bottom_limit - exp_line_h:
                break
            _draw_centered(draw, sy, wrapped, explainer_font, CREAM, tracking=0)
            sy += exp_line_h


def _layout_cta(draw: ImageDraw.ImageDraw, fonts: dict, spec: PostSpec) -> None:
    """
    CTA / booking-area post: big short hook + 1-line where/when + explainer.
    Used for "Booking DFW this weekend" / "Heading to Coppell Thursday" angles.
    """
    # Hook handles 1–3 lines; this is usually 1 line.
    hook_font = fonts["hook_big"] if len(spec.hook) == 1 else fonts["hook_med"]
    line_h = 110 if hook_font is fonts["hook_big"] else 82

    total_hook_h = line_h * len(spec.hook)
    hook_start_y = 580 - (total_hook_h // 2) + (line_h // 2)

    for i, line in enumerate(spec.hook):
        _draw_centered(draw, hook_start_y + i * line_h, line, hook_font, CREAM, tracking=1)

    div_y = hook_start_y + total_hook_h + 14
    draw.line([(CANVAS_SIZE // 2 - 60, div_y), (CANVAS_SIZE // 2 + 60, div_y)],
              fill=GOLD, width=2)

    # Body: typically 1–2 lines of where/when (wrap to safe width, bounded by phone block)
    body_safe_w = CANVAS_SIZE - (2 * MARGIN) - 30
    bottom_limit = CANVAS_SIZE - 200
    sy = div_y + 28
    for line in spec.body:
        for wrapped in _wrap_to_width(line, fonts["body"], body_safe_w):
            if sy > bottom_limit - 34:
                break
            _draw_centered(draw, sy, wrapped, fonts["body"], CREAM, tracking=0)
            sy += 34
        sy += 4

    if spec.explainer and sy + 30 < bottom_limit:
        sy += 8
        for wrapped in _wrap_to_width(spec.explainer, fonts["explainer"], body_safe_w):
            if sy > bottom_limit - 30:
                break
            _draw_centered(draw, sy, wrapped, fonts["explainer"], CREAM, tracking=0)
            sy += 30


def _layout_quote(draw: ImageDraw.ImageDraw, fonts: dict, spec: PostSpec) -> None:
    """
    Quote / philosophy post: a large serif quote in italics (rendered as plain serif),
    one-line attribution, no body bullets. Used for brand-voice posts.
    """
    quote_safe_w = CANVAS_SIZE - (2 * MARGIN) - 30
    quote_font = fonts["hook_med"]

    # Bracket the full quote with open/close marks, then word-wrap to safe width
    raw_text = " ".join(spec.hook)
    full = f'"{raw_text}"'
    wrapped = _wrap_to_width(full, quote_font, quote_safe_w, tracking=1)

    # If wrapping ballooned to many lines, shrink the font so it stays balanced
    while len(wrapped) > 4 and quote_font.size > 48:
        quote_font = _font(spec.playfair_path, quote_font.size - 6, 900)
        wrapped = _wrap_to_width(full, quote_font, quote_safe_w, tracking=1)

    line_h = max(54, int(quote_font.size * 1.15))
    total_h = line_h * len(wrapped)
    start_y = 540 - (total_h // 2) + (line_h // 2)

    for i, line in enumerate(wrapped):
        _draw_centered(draw, start_y + i * line_h, line, quote_font, CREAM, tracking=1)

    # Attribution line under quote (uses explainer slot, wrapped to safe width)
    if spec.explainer:
        attr_safe_w = CANVAS_SIZE - (2 * MARGIN) - 30
        attr_y = start_y + total_h + 30
        wrapped = _wrap_to_width(f"— {spec.explainer}", fonts["explainer"], attr_safe_w, tracking=2)
        for line in wrapped:
            _draw_centered(draw, attr_y, line, fonts["explainer"], CREAM, tracking=2)
            attr_y += 32


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="Render a Short Go post.")
    p.add_argument("--spec", required=True, help="Path to JSON file with PostSpec fields.")
    p.add_argument("--out", required=True, help="Output PNG path.")
    args = p.parse_args()

    spec_data = json.loads(Path(args.spec).read_text())
    spec_data["background_path"] = Path(spec_data["background_path"])
    if "playfair_path" in spec_data:
        spec_data["playfair_path"] = Path(spec_data["playfair_path"])
    if "inter_path" in spec_data:
        spec_data["inter_path"] = Path(spec_data["inter_path"])

    spec = PostSpec(**spec_data)
    out = compose_post(spec, Path(args.out))
    print(f"Saved: {out}")
