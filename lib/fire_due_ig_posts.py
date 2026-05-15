"""
Short Go agent — IG due-post firing cron.

Instagram doesn't natively schedule image posts via the Graph API. When an
approved post has a scheduled_at more than ~2 minutes in the future,
reply_watcher.fire_approved_post() defers IG firing and marks
`meta_responses.instagram.deferred = true` in the pending-approval state.

This script runs hourly, scans the pending-approval folder, and fires any
deferred IG posts whose scheduled time has arrived (within a 6-minute
tolerance window so cron-fired times line up close enough to the targeted
minute).

Run via the hourly Cowork scheduled task `short-go-ig-due-fire`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(_LIB))

from send_for_approval import PENDING_DIR, DISPLAY_TZ
from post_to_meta import post_to_instagram_now, MetaAPIError

# Fire any deferred IG post whose scheduled_at is within FIRE_WINDOW of now
FIRE_WINDOW = timedelta(minutes=8)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | str) -> datetime:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(dt.replace("Z", "+00:00"))


def list_due_ig_posts() -> list[Path]:
    """Return paths of pending-approval files that need IG firing now."""
    if not PENDING_DIR.exists():
        return []
    due: list[Path] = []
    now = _now()
    for path in PENDING_DIR.glob("*.json"):
        try:
            state = json.loads(path.read_text())
        except Exception:
            continue
        if state.get("status") != "approved":
            continue
        meta_resp = state.get("meta_responses") or {}
        ig_resp = meta_resp.get("instagram") or {}
        # Already fired (success or failure)? skip.
        if "post_id" in ig_resp or ig_resp.get("ok") is False:
            continue
        # Not deferred? skip (means IG either fired with FB or wasn't requested)
        if not ig_resp.get("deferred"):
            continue
        scheduled_at = state.get("webhook_payload", {}).get("schedule_at") or state.get("scheduled_at")
        if not scheduled_at:
            continue
        sched = _iso(scheduled_at)
        delta = (sched - now).total_seconds()
        # Fire if scheduled time is within FIRE_WINDOW seconds (past or imminent)
        if delta <= FIRE_WINDOW.total_seconds() and delta >= -3600:
            due.append(path)
    return due


def fire_one(path: Path) -> dict:
    """Fire one IG post and update the state file."""
    state = json.loads(path.read_text())
    payload = state["webhook_payload"]
    image_url = payload["image_url"]
    caption = payload["caption"]

    try:
        result = post_to_instagram_now(image_url, caption)
        state.setdefault("meta_responses", {})["instagram"] = result
        state["ig_fired_at"] = datetime.now(DISPLAY_TZ).isoformat()
    except MetaAPIError as e:
        state.setdefault("meta_responses", {})["instagram"] = {
            "error": str(e),
            "ok": False,
            "attempted_at": datetime.now(DISPLAY_TZ).isoformat(),
        }
    path.write_text(json.dumps(state, indent=2))
    return state["meta_responses"]["instagram"]


def main() -> int:
    due = list_due_ig_posts()
    if not due:
        print("No IG posts due to fire.")
        return 0
    print(f"Firing {len(due)} IG post(s):")
    for p in due:
        post_id = p.stem
        print(f"  - {post_id} ... ", end="", flush=True)
        result = fire_one(p)
        if "post_id" in result:
            print(f"OK (ig_post_id={result['post_id']})")
        else:
            print(f"FAIL: {result.get('error', result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
