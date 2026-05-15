"""
Short Go agent — reply watcher.

Runs hourly (or on-demand). Reads recent iMessages from Charles, parses
APPROVE / REJECT replies, and acts on matching pending posts:

    APPROVE 2026-05-20-edu-001
        → fires queue_to_make.fire_webhook with the stored payload
        → updates pending state: status="approved", fired_at, webhook_response

    REJECT 2026-05-20-cta-002 [optional reason]
        → updates pending state: status="rejected", rejected_reason
        → appends a row to content-log.md so we learn what Charles rejects

The actual iMessage reading happens via the iMessage MCP tool (the agent's
job). This module supplies the parsing and state-mutation utilities.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

_LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(_LIB))

from send_for_approval import PENDING_DIR, DISPLAY_TZ
from queue_to_make import fire_webhook   # legacy — Make webhook (kept for fallback only)

# Direct-to-Meta path (preferred since 2026-05-15)
try:
    from post_to_meta import schedule_facebook_post, post_to_instagram_now, MetaAPIError
    META_DIRECT_AVAILABLE = True
except Exception:
    META_DIRECT_AVAILABLE = False

CONTENT_LOG = _LIB.parent / "content-log.md"

# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------

# Match the FIRST occurrence of APPROVE or REJECT, then capture everything after
# as the optional notes. The agent figures out which post_id the reply applies to
# from conversational context (the iMessage Charles is replying to).
#
# Tolerates: "approve", "APPROVE!", "Approve — love this one", etc.
_APPROVE_RE = re.compile(r"\bAPPROVE\b[\s\.\,\!\:\-—]*(.*)?", re.IGNORECASE | re.DOTALL)
_REJECT_RE = re.compile(r"\bREJECT\b[\s\.\,\!\:\-—]*(.*)?", re.IGNORECASE | re.DOTALL)


@dataclass
class ParsedReply:
    action: str             # "APPROVE" or "REJECT"
    notes: str | None       # anything Charles typed after the keyword — feedback / why


def parse_reply(message_text: str) -> ParsedReply | None:
    """Extract APPROVE/REJECT command + any free-text notes from an iMessage body.

    Returns None if the text isn't an approval command.

    Charles's UX: just type APPROVE or REJECT, optionally with feedback after.
    The agent figures out WHICH post by looking at the most recent approval-
    request iMessage it sent — see process_reply().
    """
    if not message_text:
        return None
    text = message_text.strip()

    # REJECT wins if both keywords appear — safer default for ambiguous input
    m = _REJECT_RE.search(text)
    if m:
        notes = (m.group(1) or "").strip().rstrip(".,!:- —") or None
        return ParsedReply(action="REJECT", notes=notes)

    m = _APPROVE_RE.search(text)
    if m:
        notes = (m.group(1) or "").strip().rstrip(".,!:- —") or None
        return ParsedReply(action="APPROVE", notes=notes)

    return None


# ---------------------------------------------------------------------------
# Pending state I/O
# ---------------------------------------------------------------------------

def load_pending_state(post_id: str) -> dict | None:
    p = PENDING_DIR / f"{post_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_pending_state(post_id: str, state: dict) -> Path:
    p = PENDING_DIR / f"{post_id}.json"
    p.write_text(json.dumps(state, indent=2))
    return p


def list_awaiting_approval() -> list[dict]:
    """All pending posts that haven't yet been approved or rejected."""
    if not PENDING_DIR.exists():
        return []
    out = []
    for p in sorted(PENDING_DIR.glob("*.json")):
        s = json.loads(p.read_text())
        if s.get("status") == "awaiting_approval":
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(DISPLAY_TZ).isoformat()


def mark_approved(post_id: str, notes: str | None = None, webhook_response: dict | None = None) -> dict:
    state = load_pending_state(post_id)
    if state is None:
        raise FileNotFoundError(f"No pending state for {post_id}")
    if state["status"] != "awaiting_approval":
        # Idempotency — already acted on; don't overwrite
        return state
    state["status"] = "approved"
    state["approved_at"] = _now()
    state["approved_via"] = "imessage"
    state["charles_notes"] = notes      # feedback text on approval, if any
    if webhook_response is not None:
        state["fired_at"] = _now()
        state["webhook_response"] = webhook_response
    save_pending_state(post_id, state)
    if notes:
        _append_approval_notes_to_content_log(state)
    return state


def mark_rejected(post_id: str, notes: str | None) -> dict:
    """Mark a post as rejected. `notes` captures Charles's freeform feedback."""
    state = load_pending_state(post_id)
    if state is None:
        raise FileNotFoundError(f"No pending state for {post_id}")
    if state["status"] != "awaiting_approval":
        return state
    state["status"] = "rejected"
    state["rejected_at"] = _now()
    state["rejected_reason"] = notes
    state["charles_notes"] = notes
    save_pending_state(post_id, state)
    _append_rejection_to_content_log(state)
    return state


# ---------------------------------------------------------------------------
# Approval-action: fire the Make webhook for an approved post
# ---------------------------------------------------------------------------

def fire_approved_post(post_id: str) -> dict:
    """Take a post that's just been approved and publish/schedule it on Meta.

    Preferred path (since 2026-05-15): direct Meta Graph API via post_to_meta.
        - Facebook Page: schedule_facebook_post() — Meta handles scheduling natively
        - Instagram (when IG_USER_ID is configured): post_to_instagram_now() — fires immediately
          (IG doesn't support native scheduling for image posts via the Graph API)

    Fallback path (legacy): Make.com webhook (only if META_DIRECT_AVAILABLE is False).

    Returns the merged state including the API response. Updates the JSON.
    """
    state = load_pending_state(post_id)
    if state is None:
        raise FileNotFoundError(f"No pending state for {post_id}")
    if state["status"] != "approved":
        raise RuntimeError(
            f"Post {post_id} status is {state['status']!r}, not 'approved'. "
            "Approve before firing."
        )
    if state.get("fired_at"):
        # Idempotency — already fired
        return state

    if META_DIRECT_AVAILABLE:
        # Direct Meta API path — no middleware
        responses = {}
        payload = state["webhook_payload"]
        image_url = payload["image_url"]
        caption = payload["caption"]
        scheduled_at = payload["schedule_at"]
        channels = (payload.get("channel") or "facebook,instagram").split(",")

        if "facebook" in channels:
            try:
                fb = schedule_facebook_post(image_url, caption, scheduled_at)
                responses["facebook"] = fb
            except Exception as e:
                responses["facebook"] = {"error": str(e), "ok": False}

        if "instagram" in channels:
            try:
                # IG doesn't schedule — only post-now. If we're firing in advance,
                # the agent should fire IG at the scheduled time from its cron.
                # For now: if scheduled_at is within 2 minutes, fire IG now; else defer.
                from datetime import datetime, timezone as _tz
                dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                now = datetime.now(_tz.utc)
                if (dt - now).total_seconds() <= 120:
                    ig = post_to_instagram_now(image_url, caption)
                    responses["instagram"] = ig
                else:
                    responses["instagram"] = {
                        "deferred": True,
                        "reason": "IG doesn't schedule natively; fire at scheduled_at from cron",
                        "scheduled_at": scheduled_at,
                    }
            except MetaAPIError as e:
                responses["instagram"] = {"error": str(e), "ok": False}
            except Exception as e:
                responses["instagram"] = {"error": f"{type(e).__name__}: {e}", "ok": False}

        state["fired_at"] = _now()
        state["meta_responses"] = responses
        save_pending_state(post_id, state)
        return state
    else:
        # Legacy: Make.com webhook (kept as fallback if post_to_meta isn't loaded)
        payload = state["webhook_payload"]
        response = fire_webhook(payload)
        state["fired_at"] = _now()
        state["webhook_response"] = response
        save_pending_state(post_id, state)
        return state


# ---------------------------------------------------------------------------
# Content log
# ---------------------------------------------------------------------------

def _append_rejection_to_content_log(state: dict) -> None:
    """Log rejections to content-log.md so future agent runs learn what's off-brand."""
    if not CONTENT_LOG.exists():
        return
    line = (
        f"{datetime.now(DISPLAY_TZ).date().isoformat()} | "
        f"REJECTED | {state.get('post_type','?')} | {state.get('hook','')!r} | "
        f"notes: {state.get('charles_notes') or '(no notes)'}\n"
    )
    content = CONTENT_LOG.read_text()
    if "## REJECTED" in content:
        content = content.replace("## REJECTED\n", "## REJECTED\n" + line, 1)
    else:
        content = content.rstrip() + "\n\n## REJECTED\n" + line
    CONTENT_LOG.write_text(content)


def _append_approval_notes_to_content_log(state: dict) -> None:
    """Log approval feedback so future agent runs reinforce what Charles likes."""
    if not CONTENT_LOG.exists():
        return
    line = (
        f"{datetime.now(DISPLAY_TZ).date().isoformat()} | "
        f"APPROVED w/ notes | {state.get('post_type','?')} | {state.get('hook','')!r} | "
        f"notes: {state.get('charles_notes')}\n"
    )
    content = CONTENT_LOG.read_text()
    if "## APPROVED WITH NOTES" in content:
        content = content.replace("## APPROVED WITH NOTES\n", "## APPROVED WITH NOTES\n" + line, 1)
    else:
        content = content.rstrip() + "\n\n## APPROVED WITH NOTES\n" + line
    CONTENT_LOG.write_text(content)


# ---------------------------------------------------------------------------
# Driver — what the watcher agent runs per fired message
# ---------------------------------------------------------------------------

def process_reply(message_text: str, post_id: str) -> dict:
    """End-to-end processing of one inbound iMessage reply against a specific post.

    The agent (Claude in the watcher session) figures out WHICH post_id a reply
    applies to using conversational context (the iMessage Charles is replying to
    most likely refers to the approval message we sent right before his reply).
    This function takes that post_id explicitly and applies the action.

    Returns a dict describing what happened:
        {"action": "approved|rejected|ignored|stale|fired",
         "post_id": str,
         "notes": str or None,
         "details": str}
    """
    parsed = parse_reply(message_text)
    if parsed is None:
        return {"action": "ignored", "post_id": post_id, "notes": None,
                "details": "no APPROVE/REJECT keyword found"}

    state = load_pending_state(post_id)
    if state is None:
        return {
            "action": "stale",
            "post_id": post_id,
            "notes": parsed.notes,
            "details": f"no pending post with id {post_id} found",
        }

    if state["status"] != "awaiting_approval":
        return {
            "action": "stale",
            "post_id": post_id,
            "notes": parsed.notes,
            "details": f"post already {state['status']!r}; ignoring",
        }

    if parsed.action == "APPROVE":
        mark_approved(post_id, notes=parsed.notes)
        fired = fire_approved_post(post_id)
        return {
            "action": "fired",
            "post_id": post_id,
            "notes": parsed.notes,
            "details": "approved + webhook fired",
            "webhook_response": fired.get("webhook_response"),
        }
    elif parsed.action == "REJECT":
        mark_rejected(post_id, notes=parsed.notes)
        return {
            "action": "rejected",
            "post_id": post_id,
            "notes": parsed.notes,
            "details": f"notes: {parsed.notes or '(none)'}",
        }

    return {"action": "ignored", "post_id": post_id, "notes": None, "details": "unhandled action"}


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Reply-watcher manual driver.")
    sub = p.add_subparsers(dest="cmd")

    p_parse = sub.add_parser("parse", help="Parse a message string and print the result.")
    p_parse.add_argument("message")

    p_process = sub.add_parser("process", help="Run process_reply on a message string against a specific post_id.")
    p_process.add_argument("post_id")
    p_process.add_argument("message")

    p_list = sub.add_parser("list-pending", help="List all posts awaiting approval.")

    args = p.parse_args()
    if args.cmd == "parse":
        r = parse_reply(args.message)
        print(json.dumps(r.__dict__ if r else None, indent=2))
    elif args.cmd == "process":
        print(json.dumps(process_reply(args.message, args.post_id), indent=2))
    elif args.cmd == "list-pending":
        items = list_awaiting_approval()
        print(json.dumps([{
            "post_id": s["post_id"],
            "post_type": s["post_type"],
            "scheduled_at": s.get("scheduled_at"),
            "sent_at": s.get("sent_at"),
        } for s in items], indent=2))
    else:
        p.print_help()
