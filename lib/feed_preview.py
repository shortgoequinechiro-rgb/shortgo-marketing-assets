"""
Short Go agent — feed-preview renderer.

Takes a rendered post PNG (from compose_post.py) + a caption, produces a
mockup of what the post will look like in an Instagram feed. Used by the
approval-send step so Charles sees the post in context (header, image, action
bar, truncated caption) instead of just the bare 1080x1080.

This is approval-only — never gets uploaded to IG/FB. It's a tool for Charles
to judge "would I scroll past this or stop?".

Layout (target width 1080):

    ┌──────────────────────────────────────┐
    │ [logo]  shortgoequinechiro         …│   80px header
    │         DFW · North Texas            │
    ├──────────────────────────────────────┤
    │                                      │
    │      [rendered 1080x1080 post]       │   1080px image
    │                                      │
    ├──────────────────────────────────────┤
    │ ♡  ▢  ◭                          ⌶  │   50px action bar
    │                                      │
    │ Liked by drlea_dc and 23 others      │
    │                                      │
    │ shortgoequinechiro You see it on the │
    │ run-out. Drifts wide, lead's late,   │   caption section (~250px)
    │ lands off behind into the alley...   │
    │ more                                  │
    │                                      │
    │ 2 HOURS AGO                          │
    └──────────────────────────────────────┘
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Reuse the bundled fonts from compose_post
_LIB = Path(__file__).resolve().parent
INTER = _LIB / "fonts" / "Inter.ttf"

# IG-typical colors
BG = (255, 255, 255, 255)
TEXT_DARK = (38, 38, 38, 255)
TEXT_GREY = (115, 115, 115, 255)
TEXT_LIGHT_GREY = (168, 168, 168, 255)
DIVIDER = (239, 239, 239, 255)

# Layout constants
CARD_WIDTH = 1080
HEADER_HEIGHT = 95
ACTION_BAR_HEIGHT = 60
CAPTION_PAD_TOP = 20
LIKED_BY_LINE_HEIGHT = 28
CAPTION_LINE_HEIGHT = 30
CAPTION_PAD_BOTTOM = 18
TIMESTAMP_HEIGHT = 36

H_MARGIN = 28


def _font(size: int, weight: int = 500) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(INTER), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap a string to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def _draw_circle_avatar(canvas: Image.Image, x: int, y: int, size: int, initials: str = "SG") -> None:
    """Simple branded avatar placeholder — earthy color circle with SG initials.
    Production: swap in shortgochiro.com profile pic."""
    ring = Image.new("RGBA", (size + 6, size + 6), (0, 0, 0, 0))
    ring_d = ImageDraw.Draw(ring)
    # IG gradient ring (warm story-ring style)
    ring_d.ellipse((0, 0, size + 5, size + 5), outline=(207, 145, 50, 255), width=3)
    canvas.paste(ring, (x - 3, y - 3), ring)

    avatar = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    av_d = ImageDraw.Draw(avatar)
    # Saddle-brown fill matching brand
    av_d.ellipse((0, 0, size - 1, size - 1), fill=(99, 65, 41, 255))
    font = _font(int(size * 0.42), 800)
    bbox = font.getbbox(initials)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    av_d.text(
        ((size - text_w) // 2 - bbox[0], (size - text_h) // 2 - bbox[1] - 1),
        initials, font=font, fill=(252, 245, 230, 255),
    )
    canvas.paste(avatar, (x, y), avatar)


def _draw_action_bar(canvas: Image.Image, draw: ImageDraw.ImageDraw, y: int) -> None:
    """IG-style action icon row: heart, comment, share + (right) bookmark.
    Drawn as simple outlined shapes — no real icon set needed."""
    icon_size = 30
    spacing = 50
    line_w = 2.5

    # LEFT cluster: heart, comment bubble, paper plane
    x = H_MARGIN
    # Heart (two arcs + V)
    draw.line([(x, y + icon_size // 2), (x + icon_size // 2, y + icon_size - 4)],
              fill=TEXT_DARK, width=int(line_w))
    draw.line([(x + icon_size // 2, y + icon_size - 4), (x + icon_size, y + icon_size // 2)],
              fill=TEXT_DARK, width=int(line_w))
    draw.arc([(x, y - 2), (x + icon_size, y + icon_size - 4)], 180, 360,
             fill=TEXT_DARK, width=int(line_w))
    x += spacing + icon_size

    # Comment bubble (rounded square)
    draw.rounded_rectangle([(x, y), (x + icon_size, y + icon_size)],
                           radius=8, outline=TEXT_DARK, width=int(line_w))
    # Small tail
    draw.line([(x + 6, y + icon_size), (x + 4, y + icon_size + 6)],
              fill=TEXT_DARK, width=int(line_w))
    x += spacing + icon_size

    # Paper plane (triangle)
    draw.polygon([
        (x, y + icon_size // 2),
        (x + icon_size, y),
        (x + icon_size - 4, y + icon_size // 2),
        (x + icon_size, y + icon_size),
    ], outline=TEXT_DARK, width=int(line_w))

    # RIGHT cluster: bookmark
    bx = CARD_WIDTH - H_MARGIN - icon_size
    draw.polygon([
        (bx, y),
        (bx + icon_size, y),
        (bx + icon_size, y + icon_size),
        (bx + icon_size // 2, y + icon_size - 8),
        (bx, y + icon_size),
    ], outline=TEXT_DARK, width=int(line_w))


def render_feed_preview(
    rendered_post_path: Path,
    caption: str,
    output_path: Path,
    handle: str = "shortgoequinechiro",
    handle_sub: str = "DFW · North Texas · Mobile Chiro",
    timestamp_label: str = "2 HOURS AGO",
    fake_likes_line: str = "Liked by drlea_dc and 23 others",
    caption_max_lines: int = 3,
) -> Path:
    """Compose an IG feed-card mockup and save to output_path."""

    # Load the rendered post (already 1080x1080 from compose_post)
    post_img = Image.open(rendered_post_path).convert("RGB")
    if post_img.size != (CARD_WIDTH, CARD_WIDTH):
        # Resize defensively if the input is some other size
        post_img = post_img.resize((CARD_WIDTH, CARD_WIDTH), Image.LANCZOS)

    # Fonts
    handle_font = _font(28, 700)
    sub_font = _font(20, 400)
    caption_handle_font = _font(24, 700)
    caption_body_font = _font(24, 400)
    liked_by_font = _font(22, 600)
    timestamp_font = _font(18, 500)

    # Word-wrap caption to determine total caption block height
    caption_max_width = CARD_WIDTH - 2 * H_MARGIN
    # Combine handle + caption first sentence inline like IG does
    # (caption text starts on same line as handle, wraps from there)
    handle_prefix = handle + " "
    handle_prefix_w = caption_handle_font.getbbox(handle_prefix)[2]
    # Wrap remainder using width minus handle prefix on first line only
    full_caption = caption.replace("\n\n", " ").replace("\n", " ").strip()
    body_lines = _wrap_text(full_caption, caption_body_font, caption_max_width - handle_prefix_w)
    # If first line is short enough to share with handle, keep them combined;
    # otherwise let handle stand alone on first row.
    # Limit to caption_max_lines.
    visible_body = body_lines[:caption_max_lines]
    truncated = len(body_lines) > caption_max_lines

    caption_block_h = (
        CAPTION_PAD_TOP
        + LIKED_BY_LINE_HEIGHT
        + 12
        + CAPTION_LINE_HEIGHT * max(len(visible_body), 1)
        + (CAPTION_LINE_HEIGHT if truncated else 0)
        + CAPTION_PAD_BOTTOM
    )

    total_h = (
        HEADER_HEIGHT
        + CARD_WIDTH
        + ACTION_BAR_HEIGHT
        + caption_block_h
        + TIMESTAMP_HEIGHT
    )

    canvas = Image.new("RGBA", (CARD_WIDTH, total_h), BG)
    draw = ImageDraw.Draw(canvas)

    # === HEADER ===
    _draw_circle_avatar(canvas, H_MARGIN, 18, 60)
    text_x = H_MARGIN + 60 + 18
    draw.text((text_x, 20), handle, font=handle_font, fill=TEXT_DARK)
    draw.text((text_x, 56), handle_sub, font=sub_font, fill=TEXT_GREY)
    # "…" menu top-right
    dots_font = _font(34, 700)
    draw.text((CARD_WIDTH - H_MARGIN - 24, 22), "…", font=dots_font, fill=TEXT_DARK)
    # Bottom divider line for header
    draw.line([(0, HEADER_HEIGHT - 1), (CARD_WIDTH, HEADER_HEIGHT - 1)], fill=DIVIDER, width=1)

    # === IMAGE ===
    canvas.paste(post_img, (0, HEADER_HEIGHT))

    # === ACTION BAR ===
    action_y = HEADER_HEIGHT + CARD_WIDTH + 14
    _draw_action_bar(canvas, draw, action_y)

    # === CAPTION BLOCK ===
    caption_y = HEADER_HEIGHT + CARD_WIDTH + ACTION_BAR_HEIGHT + CAPTION_PAD_TOP
    # Liked by line
    draw.text((H_MARGIN, caption_y), fake_likes_line, font=liked_by_font, fill=TEXT_DARK)
    caption_y += LIKED_BY_LINE_HEIGHT + 12

    # Handle inline with first line of caption
    first_line = visible_body[0] if visible_body else ""
    draw.text((H_MARGIN, caption_y), handle_prefix, font=caption_handle_font, fill=TEXT_DARK)
    draw.text((H_MARGIN + handle_prefix_w, caption_y), first_line, font=caption_body_font, fill=TEXT_DARK)
    caption_y += CAPTION_LINE_HEIGHT

    # Remaining lines
    for line in visible_body[1:]:
        draw.text((H_MARGIN, caption_y), line, font=caption_body_font, fill=TEXT_DARK)
        caption_y += CAPTION_LINE_HEIGHT

    if truncated:
        # "...more" link in IG's grey
        draw.text((H_MARGIN, caption_y), "more", font=caption_body_font, fill=TEXT_LIGHT_GREY)
        caption_y += CAPTION_LINE_HEIGHT

    # === TIMESTAMP ===
    ts_y = HEADER_HEIGHT + CARD_WIDTH + ACTION_BAR_HEIGHT + caption_block_h + 6
    draw.text((H_MARGIN, ts_y), timestamp_label, font=timestamp_font, fill=TEXT_LIGHT_GREY)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Render IG feed-preview mockup.")
    p.add_argument("--post", required=True, help="Path to the rendered 1080x1080 post PNG.")
    p.add_argument("--caption", required=True, help="Caption text (will be word-wrapped + truncated).")
    p.add_argument("--out", required=True, help="Output PNG path.")
    args = p.parse_args()
    out = render_feed_preview(Path(args.post), args.caption, Path(args.out))
    print(f"Saved feed preview: {out}")
