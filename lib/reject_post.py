"""
Reject-button handler.

Triggered from the cloud reject workflow when a GitHub issue is opened/labeled
with "reject". Parses the issue title (format: `REJECT <post_id>`), deletes the
matching FB scheduled post, removes the post from `state/pending-ig.json`, and
returns a short status string for the workflow to comment back to the issue.

Reads creds from env: META_PAGE_ACCESS_TOKEN, META_PAGE_ID, META_IG_USER_ID.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))

# Repo root resolution: rely on GITHUB_WORKSPACE in CI; fall back to ancestor walk.
def _repo_root() -> Path:
    gha = os.environ.get("GITHUB_WORKSPACE")
    if gha and (Path(gha) / ".git").exists():
        return Path(gha)
    here = _THIS.parent
    for ancestor in [here, *here.parents]:
        if (ancestor / ".git").exists() and (ancestor / "lib").exists():
            return ancestor
    raise SystemExit("Repo root not found")


REPO_ROOT = _repo_root()
STATE_FILE = REPO_ROOT / "state" / "pending-ig.json"
SCHEDULED_INDEX = REPO_ROOT / "state" / "scheduled-fb-index.json"  # optional cache: post_id -> fb_post_id


POST_ID_RE = re.compile(r"REJECT\s+([A-Za-z0-9_\-:]+)")


def parse_post_id(issue_title: str) -> str | None:
    """Pull '2026-05-20-edu-haul-recovery' out of 'REJECT 2026-05-20-edu-haul-recovery'."""
    m = POST_ID_RE.search(issue_title or "")
    return m.group(1) if m else None


def _load_scheduled_index() -> dict:
    """Optional: a sidecar map of post_id -> fb_post_id, written by sunday_cloud_agent.

    If missing, we fall back to listing all FB scheduled posts and matching by caption.
    """
    if SCHEDULED_INDEX.exists():
        try:
            return json.loads(SCHEDULED_INDEX.read_text())
        except Exception:
            return {}
    return {}


def _save_scheduled_index(idx: dict) -> None:
    SCHEDULED_INDEX.parent.mkdir(exist_ok=True)
    SCHEDULED_INDEX.write_text(json.dumps(idx, indent=2))


def reject(post_id: str) -> dict:
    """Delete the FB scheduled post + remove the IG defer for this post_id.

    Returns a status dict for the caller to surface.
    """
    from post_to_meta import (
        MetaCreds,
        delete_facebook_post,
        list_scheduled_facebook_posts,
    )

    creds = MetaCreds(
        page_id=os.environ.get("META_PAGE_ID", ""),
        page_name="Short Go Equine Chiropractic",
        page_access_token=os.environ.get("META_PAGE_ACCESS_TOKEN", ""),
        ig_user_id=os.environ.get("META_IG_USER_ID", ""),
    )
    if not creds.page_access_token:
        return {"ok": False, "reason": "META_PAGE_ACCESS_TOKEN missing"}

    result: dict = {"post_id": post_id, "fb_deleted": None, "ig_dequeued": False, "errors": []}

    # 1) FB delete — prefer index lookup, fall back to message scan.
    idx = _load_scheduled_index()
    fb_post_id = idx.get(post_id)
    if fb_post_id:
        try:
            delete_facebook_post(fb_post_id, creds)
            result["fb_deleted"] = fb_post_id
            idx.pop(post_id, None)
            _save_scheduled_index(idx)
        except Exception as e:
            result["errors"].append(f"FB delete by index failed: {e}")
            fb_post_id = None  # try scan below
    if not result["fb_deleted"]:
        # Scan all scheduled posts; match if the post_id appears anywhere in the message body.
        try:
            scanned = list_scheduled_facebook_posts(creds)
            for p in scanned:
                msg = p.get("message") or ""
                if post_id in msg:
                    delete_facebook_post(p["id"], creds)
                    result["fb_deleted"] = p["id"]
                    break
            if not result["fb_deleted"] and scanned:
                result["errors"].append(
                    f"No FB scheduled post matched post_id={post_id} (scanned {len(scanned)})"
                )
        except Exception as e:
            result["errors"].append(f"FB scan failed: {e}")

    # 2) IG defer queue prune
    if STATE_FILE.exists():
        try:
            pending = json.loads(STATE_FILE.read_text())
            before = len(pending)
            pending = [e for e in pending if e.get("post_id") != post_id]
            after = len(pending)
            if after < before:
                STATE_FILE.write_text(json.dumps(pending, indent=2))
                result["ig_dequeued"] = True
            else:
                result["errors"].append(f"post_id={post_id} was not in pending-ig.json")
        except Exception as e:
            result["errors"].append(f"IG state update failed: {e}")
    else:
        result["errors"].append("pending-ig.json not present")

    result["ok"] = bool(result["fb_deleted"] or result["ig_dequeued"])
    return result


def format_comment(status: dict) -> str:
    """Render a short markdown comment to leave on the issue."""
    pid = status.get("post_id") or "(unknown)"
    if status.get("ok"):
        lines = [f"✅ Rejected `{pid}`."]
        if status.get("fb_deleted"):
            lines.append(f"- Facebook scheduled post deleted (`{status['fb_deleted']}`).")
        if status.get("ig_dequeued"):
            lines.append("- Removed from Instagram defer queue.")
        if status.get("errors"):
            lines.append("")
            lines.append("Non-fatal notes:")
            for e in status["errors"]:
                lines.append(f"- {e}")
        return "\n".join(lines)
    else:
        lines = [f"⚠️ Could not reject `{pid}`."]
        for e in status.get("errors", []) or ["no details"]:
            lines.append(f"- {e}")
        return "\n".join(lines)


def main() -> int:
    title = os.environ.get("ISSUE_TITLE", "") or (sys.argv[1] if len(sys.argv) > 1 else "")
    pid = parse_post_id(title)
    if not pid:
        print(json.dumps({"ok": False, "reason": f"Could not parse post_id from title: {title!r}"}))
        return 1
    status = reject(pid)
    print(json.dumps(status, indent=2))
    # Also write a markdown comment to /tmp for the workflow step to read
    Path("/tmp/reject_comment.md").write_text(format_comment(status))
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
