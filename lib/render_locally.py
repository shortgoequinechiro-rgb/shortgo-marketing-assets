"""
Local render harness — preview compose_post changes without spending an API call.

Renders one of each template (educational / cta / quote) with frozen sample
content into /tmp so you can inspect text overflow, legibility, and layout.

Usage (from repo root):
    python3 lib/render_locally.py

Outputs:
    /tmp/shortgo_preview_educational.png
    /tmp/shortgo_preview_cta.png
    /tmp/shortgo_preview_quote.png
    /tmp/shortgo_preview_educational_long.png  (stress test with long body)
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))

from compose_post import compose_post, PostSpec  # noqa: E402

REPO = _THIS.parent.parent
PHOTOS = REPO / "photo-library"
OUT = Path("/tmp")


def pick_bg(index: int = 0) -> Path:
    """Pick a deterministic background from photo-library."""
    pngs = sorted(PHOTOS.glob("*.png"))
    if not pngs:
        raise SystemExit(f"No backgrounds found at {PHOTOS}")
    return pngs[index % len(pngs)]


SAMPLES: list[tuple[str, PostSpec]] = [
    (
        "educational",
        PostSpec(
            background_path=pick_bg(2),
            template="educational",
            hook=["YOUR ROPE HORSE", "IS TELLING YOU SOMETHING."],
            body=[
                "Swapping leads late — or not at all — coming back to the rope.",
                "Pulling left or right when you ask for a straight run.",
                "Dropping a shoulder on the score, every single run.",
            ],
            explainer="These aren't training problems. They're body problems.",
        ),
    ),
    (
        "cta",
        PostSpec(
            background_path=pick_bg(5),
            template="cta",
            hook=["WE COME TO YOUR BARN."],
            body=[
                "Mobile equine chiropractic across DFW and North Texas.",
                "No trailer. No stress. Just results.",
            ],
            explainer="DM 'BOOK' for the next open slot this week.",
        ),
    ),
    (
        "quote",
        PostSpec(
            background_path=pick_bg(8),
            template="quote",
            hook=['"I thought she just needed more miles. She needed Dr. Leo."'],
            explainer="Mobile equine chiropractic — Dr. Leo comes to your barn across DFW and North Texas.",
        ),
    ),
    (
        "educational_long",
        PostSpec(
            background_path=pick_bg(11),
            template="educational",
            hook=[
                "THE HAUL HOME MIGHT BE HURTING THEM MORE THAN THE RUN.",
            ],
            body=[
                "Eight hours back in a slant-load with a locked SI joint — that's not rest, that's compounding.",
                "A horse that hauled well Friday can step off stiff Sunday — and most people call it 'being tired'.",
                "Skipping the follow-up — one session is a start, not a finish.",
            ],
            explainer="Your horse can't tell you the trailer ride cost her — but her first run will.",
        ),
    ),
]


def main() -> int:
    for name, spec in SAMPLES:
        out = OUT / f"shortgo_preview_{name}.png"
        compose_post(spec, out)
        print(f"  {name:<22} -> {out}")
    print()
    print("Open the PNGs to inspect. No API calls were made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
