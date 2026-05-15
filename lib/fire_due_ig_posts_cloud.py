"""
Short Go agent — cloud-hosted hourly IG firing.

Reads state/pending-ig.json (a list of deferred IG posts), fires any whose
scheduled time has arrived (within an 8-minute window past or imminent),
and writes back the updated state.

Designed to run in GitHub Actions on an hourly cron. Credentials come from
environment variables (META_PAGE_ACCESS_TOKEN, META_IG_USER_ID).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))

# Find repo root from this file's location
REPO_ROOT = _THIS.parent.parent
STATE_FILE = REPO_ROOT / "state" / "pending-ig.json"
FIRE_WINDOW = timedelta(minutes=8)


def _iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def main() -> int:
    if not STATE_FILE.exists():
        print(f"No state file at {STATE_FILE} — nothing to fire.")
        return 0

    pending = json.loads(STATE_FILE.read_text())
    if not pending:
        print("Pending IG queue is empty.")
        return 0

    page_token = os.environ.get("META_PAGE_ACCESS_TOKEN")
    ig_user_id = os.environ.get("META_IG_USER_ID")
    if not page_token or not ig_user_id:
        print("FATAL: META_PAGE_ACCESS_TOKEN and META_IG_USER_ID env vars required.", file=sys.stderr)
        return 2

    # Import post_to_meta and inject creds via env-aware path
    from post_to_meta import post_to_instagram_now, MetaCreds, MetaAPIError
    creds = MetaCreds(
        page_id=os.environ.get("META_PAGE_ID", ""),
        page_name="Short Go Equine Chiropractic",
        page_access_token=page_token,
        ig_user_id=ig_user_id,
    )

    now = datetime.now(timezone.utc)
    remaining: list[dict] = []
    fired_count = 0

    for entry in pending:
        scheduled = _iso(entry["scheduled_at"])
        delta = (scheduled - now).total_seconds()
        # Fire if scheduled within window (past 1 hour OR up to 8 min in future)
        if delta <= FIRE_WINDOW.total_seconds() and delta >= -3600:
            print(f"Firing IG for {entry['post_id']} (scheduled_at={entry['scheduled_at']}, delta={int(delta)}s)...")
            try:
                resp = post_to_instagram_now(entry["image_url"], entry["caption"], creds=creds)
                print(f"  OK → ig_post_id={resp['post_id']}")
                fired_count += 1
                # Done — don't keep in queue
            except MetaAPIError as e:
                print(f"  FAIL: {e}", file=sys.stderr)
                # Keep in queue for retry next hour
                entry["last_error"] = str(e)
                entry["last_attempted_at"] = now.isoformat()
                remaining.append(entry)
        elif delta < -3600:
            # Too old, drop with a note (1 hour past scheduled time and never fired)
            print(f"DROPPING {entry['post_id']} — scheduled time is {int(-delta/60)} min in the past, never fired.")
        else:
            # Not yet due
            remaining.append(entry)

    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(remaining, indent=2))
    print(f"Fired {fired_count}. {len(remaining)} still queued.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
