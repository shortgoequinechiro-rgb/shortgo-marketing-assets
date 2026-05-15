"""
Short Go image host — stages rendered post PNGs for GitHub-served public URLs.

The Short Go agent posts to Instagram + Facebook via Make.com → Buffer.
Buffer's "Media URL" field fetches images by URL, so every rendered post needs
a public URL before it can be queued.

Approach:
- Public GitHub repo `shortgoequinechiro-rgb/shortgo-marketing-assets` serves as
  the image host. A local clone lives at:
  `~/Documents/Claude/Projects/ShortGoChiro/shortgo-marketing-assets/`
- The agent's workflow:
    1. compose_post.py renders the PNG → /sessions/.../mnt/.../samples/foo.png
    2. image_host.stage_image(png, key) copies the PNG into the local clone at
       the chosen key path (e.g., `posts/2026-05-15/educational-001.png`).
    3. The agent (Claude) runs `git add -A && git commit && git push` via
       osascript on Charles's Mac (where GitHub creds live).
    4. image_host.raw_url_for_key(key) returns the public URL to pass to Make.

This stays a Python module (not a full CLI) because the agent orchestrates the
osascript git step itself — much cleaner than wrapping AppleScript in Python.

Future migration: when Cloudflare R2 is enabled, swap stage_image() and
raw_url_for_key() for R2-bucket equivalents. The agent's interface stays the
same.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo location — same on Charles's Mac and in the Cowork sandbox (mounted)
# ---------------------------------------------------------------------------

# On Charles's Mac:
#   /Users/charlesdunn/Documents/Claude/Projects/ShortGoChiro/shortgo-marketing-assets/
# In Cowork sandbox bash:
#   /sessions/.../mnt/ShortGoChiro/shortgo-marketing-assets/
#
# When this module runs inside a Cowork scheduled task, we resolve the right
# one by checking which prefix exists. Both are the SAME folder via mount.
_REPO_CANDIDATES = [
    Path("/sessions/zealous-lucid-bardeen/mnt/ShortGoChiro/shortgo-marketing-assets"),
    Path.home() / "Documents/Claude/Projects/ShortGoChiro/shortgo-marketing-assets",
]

# Public repo metadata for URL construction
GITHUB_OWNER = "shortgoequinechiro-rgb"
GITHUB_REPO = "shortgo-marketing-assets"
GITHUB_BRANCH = "main"


def repo_root() -> Path:
    """Locate the local clone of the assets repo. Raises if not found."""
    for c in _REPO_CANDIDATES:
        if c.exists() and (c / ".git").exists():
            return c
    raise RuntimeError(
        f"Assets repo not found at any expected path. Tried: {_REPO_CANDIDATES}. "
        "Clone with: git clone https://github.com/shortgoequinechiro-rgb/shortgo-marketing-assets.git"
    )


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------

def build_key(post_id: str, scheduled_at: datetime | str, ext: str = "png") -> str:
    """Construct a stable repo path for a post.

    Format: posts/YYYY-MM-DD/{post_id}.{ext}

    The date is the scheduled publish date (so all posts for one day cluster
    together in the repo), not the render date.
    """
    if isinstance(scheduled_at, str):
        # Tolerate both 'Z' (UTC) and offset-aware ISO formats
        scheduled_at = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    date_str = scheduled_at.strftime("%Y-%m-%d")
    return f"posts/{date_str}/{post_id}.{ext}"


# ---------------------------------------------------------------------------
# Staging — copy a rendered PNG into the local clone at the target key
# ---------------------------------------------------------------------------

def stage_image(local_png_path: Path, key: str) -> Path:
    """Copy a rendered PNG into the assets repo at the given key.

    Creates parent directories as needed. Returns the absolute path of the
    staged file inside the local clone.

    The caller is responsible for the subsequent `git add && git commit && git push`
    step (which the agent runs via osascript on Charles's Mac).
    """
    if not local_png_path.exists():
        raise FileNotFoundError(f"Rendered PNG not found: {local_png_path}")
    if not (key.startswith("posts/") or key.startswith("previews/")):
        raise ValueError(f"Key must start with 'posts/' or 'previews/'. Got: {key}")

    target = repo_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_png_path, target)
    return target


# ---------------------------------------------------------------------------
# Public URL construction
# ---------------------------------------------------------------------------

def raw_url_for_key(key: str) -> str:
    """Return the public raw.githubusercontent.com URL for a key.

    Note: the URL is valid as soon as the file is pushed to GitHub. GitHub
    caches raw responses for ~5 minutes; first hit after push may take a few
    seconds to propagate.
    """
    return (
        f"https://raw.githubusercontent.com/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{key}"
    )


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def stage_and_return_url(local_png_path: Path, post_id: str, scheduled_at: datetime | str) -> dict:
    """All-in-one: build key, stage the file, return both repo path and public URL.

    The agent calls this after compose_post.py, then does the git push step.
    """
    key = build_key(post_id, scheduled_at)
    repo_path = stage_image(local_png_path, key)
    return {
        "key": key,
        "repo_path": str(repo_path),
        "public_url": raw_url_for_key(key),
        "git_commit_command": (
            f"cd ~/Documents/Claude/Projects/ShortGoChiro/shortgo-marketing-assets "
            f"&& git add -A "
            f"&& git -c user.email='shortgoequinechiro@gmail.com' "
            f"-c user.name='Short Go Agent' "
            f"commit -m 'Add post asset: {key}' "
            f"&& git push"
        ),
    }


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Stage a rendered post PNG for upload.")
    p.add_argument("--png", required=True, help="Path to rendered PNG.")
    p.add_argument("--post-id", required=True, help="Unique post identifier.")
    p.add_argument("--scheduled-at", required=True, help="ISO datetime when this post will publish.")
    args = p.parse_args()

    result = stage_and_return_url(
        local_png_path=Path(args.png),
        post_id=args.post_id,
        scheduled_at=args.scheduled_at,
    )
    print(json.dumps(result, indent=2))
    print("\nNext: run the git_commit_command on Charles's Mac via osascript.", file=sys.stderr)
