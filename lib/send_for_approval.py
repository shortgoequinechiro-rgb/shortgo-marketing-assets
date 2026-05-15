"""
Short Go agent — Monday-morning approval send.

The agent calls this AFTER prepare_post + git push for each of the week's 3 posts.
Each post becomes a small approval packet:
  - Renders the iMessage body text the agent will send Charles
  - Generates AppleScript to attach the image + text (run via osascript MCP)
  - Writes pending-approval state to ~/short-go-agent/pending-approval/{post_id}.json
    so the hourly reply-watcher knows what to look for

Charles replies with one of:
    APPROVE {post_id}        → agent fires queue_to_make.fire_webhook
    REJECT {post_id} [reason] → agent logs to content-log.md, skips the slot

The reply-watcher (separate scheduled task) parses these and acts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Where pending approval state lives — MUST be in a mounted folder so it persists
# across Cowork scheduled-task invocations (each invocation is a fresh sandbox).
# Both my sandbox AND Charles's Mac can read/write this path.
_STATE_CANDIDATES = [
    Path("/sessions/zealous-lucid-bardeen/mnt/ShortGoChiro/short-go-agent-state/pending-approval"),
    Path.home() / "Documents/Claude/Projects/ShortGoChiro/short-go-agent-state/pending-approval",
]

def _pending_dir() -> Path:
    """Resolve the correct pending-approval directory for this runtime."""
    for c in _STATE_CANDIDATES:
        # Check the parent exists (we'll create the leaf if needed)
        if c.parent.parent.exists():
            c.mkdir(parents=True, exist_ok=True)
            return c
    raise RuntimeError(
        f"No pending-approval directory could be created. Tried: {_STATE_CANDIDATES}"
    )


PENDING_DIR = _pending_dir()

# Where Charles's iMessage-able number is stashed (chmod 600)
CHARLES_PHONE_PATH = Path.home() / ".config/short-go-agent/charles-phone"

# Sweet-spot display tz for "scheduled for X" formatting
DISPLAY_TZ = ZoneInfo("America/Chicago")


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass
class PostApproval:
    """Everything the approval flow needs to know about one post."""
    post_id: str
    post_type: str
    hook: str                  # joined headline string
    caption: str               # IG/FB body text (full text shown for review)
    scheduled_at: str          # ISO datetime string (with tz)
    public_url: str            # raw.githubusercontent.com URL of the post image (what IG gets)
    preview_url: str           # raw.githubusercontent.com URL of the IG feed-mockup preview
    webhook_payload: dict      # ready-to-fire Make webhook body (used after APPROVE)
    image_path: str = ""       # legacy field, kept for backward compat; no longer used in send


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_charles_phone(path: Path = CHARLES_PHONE_PATH) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Charles's phone not found at {path}. "
            "Save it with: echo '+1XXXXXXXXXX' > ~/.config/short-go-agent/charles-phone"
        )
    phone = path.read_text().strip()
    if not phone.startswith("+"):
        raise ValueError(f"Phone must start with +. Got: {phone!r}")
    return phone


def format_scheduled_human(iso_str: str) -> str:
    """Format ISO datetime into Charles-readable Central-time string."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
    return dt.strftime("%a %b %-d, %-I:%M %p %Z")


def truncate_caption_preview(caption: str, max_chars: int = 220) -> str:
    """For iMessage display — keep the message scannable."""
    caption = caption.strip()
    if len(caption) <= max_chars:
        return caption
    return caption[:max_chars - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Message body
# ---------------------------------------------------------------------------

def format_message_body(approval: PostApproval, index: int, total: int) -> str:
    """The text body of the iMessage Charles receives.

    Text-only (no attachment) because iMessage refuses to deliver
    attachments to the sender's own number. Image links to GitHub raw URLs
    are tap-to-view in Safari/Photos on iPhone.
    """
    scheduled = format_scheduled_human(approval.scheduled_at)
    word_count = len(approval.caption.split())

    lines = [
        f"📅 Post {index} of {total} — {approval.post_type.title()}",
        f"Scheduled: {scheduled}",
        "",
        f"Hook: {approval.hook}",
        "",
        f"Feed preview (tap to view):",
        approval.preview_url,
        "",
        f"Caption ({word_count} words):",
        approval.caption.strip(),
        "",
        "Reply APPROVE or REJECT.",
        "Anything else you add becomes your notes on the post.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AppleScript generation
# ---------------------------------------------------------------------------

def format_send_applescript(approval: PostApproval, message_body: str, recipient: str) -> str:
    """AppleScript the agent runs via osascript MCP to send the approval iMessage.

    Text-only — iMessage refuses self-attachments to the sender's own phone,
    so we send links to the GitHub-hosted images instead.
    """
    # Escape AppleScript-quoting in the body
    escaped_body = message_body.replace("\\", "\\\\").replace('"', '\\"')

    return f'''
tell application "Messages"
    set theBuddy to buddy "{recipient}" of (service 1 whose service type = iMessage)
    send "{escaped_body}" to theBuddy
end tell
'''.strip()


# ---------------------------------------------------------------------------
# Pending state persistence
# ---------------------------------------------------------------------------

def write_pending_state(approval: PostApproval, sent_at: datetime | None = None) -> Path:
    """Save the post's metadata so the hourly reply-watcher can find it."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    state_path = PENDING_DIR / f"{approval.post_id}.json"
    state = {
        **asdict(approval),
        "status": "awaiting_approval",
        "sent_at": (sent_at or datetime.now(DISPLAY_TZ)).isoformat(),
        "approved_at": None,
        "approved_via": None,        # set when watcher matches an APPROVE reply
        "rejected_at": None,
        "rejected_reason": None,
        "fired_at": None,            # set after webhook fires
        "webhook_response": None,
    }
    state_path.write_text(json.dumps(state, indent=2))
    return state_path


# ---------------------------------------------------------------------------
# Top-level: prepare everything for one post
# ---------------------------------------------------------------------------

def prepare_approval_send(
    approval: PostApproval,
    index: int,
    total: int,
    recipient: str | None = None,
) -> dict:
    """Build the full send payload for ONE post.

    Returns dict with:
        applescript:    AppleScript the agent runs via osascript MCP
        message_body:   the text the agent will see in the iMessage
        state_path:     where the pending-approval JSON was written
        recipient:      phone number used
    """
    if recipient is None:
        recipient = load_charles_phone()
    body = format_message_body(approval, index, total)
    state_path = write_pending_state(approval)
    applescript = format_send_applescript(approval, body, recipient)
    return {
        "applescript": applescript,
        "message_body": body,
        "state_path": str(state_path),
        "recipient": recipient,
        "post_id": approval.post_id,
    }


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Prepare a post approval iMessage send.")
    p.add_argument("--approval-json", required=True, help="Path to PostApproval JSON dump.")
    p.add_argument("--index", type=int, required=True, help="Position in the week's batch (1, 2, 3).")
    p.add_argument("--total", type=int, default=3, help="Total posts in this week's batch.")
    args = p.parse_args()

    data = json.loads(Path(args.approval_json).read_text())
    approval = PostApproval(**data)
    result = prepare_approval_send(approval, args.index, args.total)
    print(json.dumps({
        "post_id": result["post_id"],
        "recipient": result["recipient"],
        "state_path": result["state_path"],
        "message_body_preview": result["message_body"][:400],
        "applescript_preview": result["applescript"][:400],
    }, indent=2))
