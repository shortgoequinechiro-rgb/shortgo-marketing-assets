"""
Short Go agent — weekly recap email.

The Sunday agent fires this once Monday morning (or end of Sunday run) with all
3 posts scheduled for the coming week. Charles reviews the email; if he wants
to nix a post, he opens any Claude session and says "kill the [date] post about
[X]" — Claude calls Meta API DELETE for the FB scheduled post and removes the
IG defer entry.

The email is informational only. No reply parsing. No iMessage. No Telegram.

Uses Resend (same provider as chirostride-app). Credentials at
~/.config/short-go-agent/notify.txt (chmod 600).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

NOTIFY_PATH = Path.home() / ".config/short-go-agent/notify.txt"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass
class NotifyCreds:
    resend_api_key: str
    to_email: str
    from_email: str
    from_name: str


def load_notify(path: Path = NOTIFY_PATH) -> NotifyCreds:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return NotifyCreds(
        resend_api_key=env["RESEND_API_KEY"],
        to_email=env["NOTIFY_TO_EMAIL"],
        from_email=env["NOTIFY_FROM_EMAIL"],
        from_name=env.get("NOTIFY_FROM_NAME", "Short Go Agent"),
    )


# ---------------------------------------------------------------------------
# Resend send
# ---------------------------------------------------------------------------

def send_resend_email(
    *,
    subject: str,
    html: str,
    text: str | None = None,
    creds: NotifyCreds | None = None,
) -> dict:
    """POST one email through Resend's API."""
    if creds is None:
        creds = load_notify()
    body = {
        "from": f"{creds.from_name} <{creds.from_email}>",
        "to": [creds.to_email],
        "subject": subject,
        "html": html,
    }
    if text:
        body["text"] = text

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {creds.resend_api_key}",
            "Content-Type": "application/json",
            # Cloudflare WAF on api.resend.com blocks the default Python-urllib
            # User-Agent (error 1010). Send a normal UA string.
            "User-Agent": "ShortGoAgent/1.0 (+https://shortgochiro.com)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend HTTP {e.code}: {err_body}") from None


# ---------------------------------------------------------------------------
# Weekly recap email
# ---------------------------------------------------------------------------

@dataclass
class PostBrief:
    """One post in the weekly recap."""
    post_id: str
    post_type: str
    hook: str
    caption: str
    image_url: str
    preview_url: str
    scheduled_at_human: str         # e.g. "Wed May 27, 2:00 PM CDT"
    fb_post_id: str | None = None   # if FB already scheduled
    ig_status: str = "deferred"     # "scheduled" | "deferred" | "fired"


def _render_failures_html(failures: list[dict]) -> str:
    """Render a '⚠️ Issues this run' block at the top of the email.

    Each failure is a dict like {'stage': 'qc'|'schedule'|'render'|'abort'|'other',
    'post_id': '...' or None, 'reason': '...', 'detail': '...' (optional)}.
    """
    if not failures:
        return ""
    rows = []
    for f in failures:
        post_id = f.get("post_id")
        stage = f.get("stage") or "other"
        reason = f.get("reason") or "(no reason given)"
        detail = f.get("detail")
        post_chip = (
            f"<code style=\"background:#fff;padding:1px 5px;border-radius:3px;font-size:11px;\">{post_id}</code>"
            if post_id else "<em>(run-level)</em>"
        )
        rows.append(
            f"<li style=\"margin-bottom:6px;\">"
            f"<strong style=\"text-transform:uppercase;letter-spacing:0.05em;font-size:11px;color:#8a1f1f;\">{stage}</strong> "
            f"{post_chip} — {reason}"
            + (f"<div style=\"color:#6a6a6a;font-size:12px;margin-top:2px;\">{detail}</div>" if detail else "")
            + "</li>"
        )
    return f"""
<tr>
  <td style="padding: 0 0 16px 0;">
    <div style="background:#fff5f5;border:1px solid #f4c1c1;border-radius:8px;padding:14px 18px;">
      <div style="font:700 14px/1.4 -apple-system,sans-serif;color:#8a1f1f;margin-bottom:6px;">
        ⚠️ Issues this run ({len(failures)})
      </div>
      <ul style="margin:0;padding-left:18px;font:400 13px/1.45 -apple-system,sans-serif;color:#3a3a3a;">
        {''.join(rows)}
      </ul>
    </div>
  </td>
</tr>
"""


def _render_failures_text(failures: list[dict]) -> list[str]:
    if not failures:
        return []
    out = ["", f"⚠️ Issues this run ({len(failures)}):"]
    for f in failures:
        post_id = f.get("post_id") or "(run-level)"
        stage = (f.get("stage") or "other").upper()
        reason = f.get("reason") or "(no reason given)"
        detail = f.get("detail")
        out.append(f"  • [{stage}] {post_id} — {reason}")
        if detail:
            out.append(f"      {detail}")
    out.append("")
    return out


def render_weekly_email_html(briefs: Iterable[PostBrief], failures: list[dict] | None = None) -> str:
    """Compose the recap email HTML."""
    briefs = list(briefs)
    failures = failures or []
    if briefs:
        intro_subject = "The post below is" if len(briefs) == 1 else f"The {len(briefs)} posts below are"
    else:
        intro_subject = "No posts were scheduled this run."
    rows = []
    for i, b in enumerate(briefs, start=1):
        # Truncate caption preview to ~250 chars
        caption_preview = b.caption.strip()
        if len(caption_preview) > 320:
            caption_preview = caption_preview[:319].rstrip() + "…"
        rows.append(f"""
<tr>
  <td style="padding: 24px 0; border-bottom: 1px solid #efefef;">
    <div style="font: 600 14px/1.4 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #8a8a8a; letter-spacing: 0.05em; text-transform: uppercase;">
      Post {i} — {b.post_type}
    </div>
    <div style="font: 700 22px/1.3 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #1c1c1c; margin-top: 4px;">
      {b.hook}
    </div>
    <div style="font: 400 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #4a4a4a; margin-top: 6px;">
      <strong>Scheduled:</strong> {b.scheduled_at_human}<br>
      <strong>Channels:</strong> Facebook (native schedule) · Instagram (deferred fire)<br>
      <strong>Post ID:</strong> <code style="background: #f5f5f5; padding: 2px 6px; border-radius: 4px; font-size: 12px;">{b.post_id}</code>
    </div>
    <div style="margin-top: 16px;">
      <a href="{b.preview_url}" style="display: block; text-decoration: none;">
        <img src="{b.preview_url}" alt="Feed preview — {b.hook}" width="480" style="display: block; width: 100%; max-width: 480px; height: auto; border-radius: 8px; border: 1px solid #e5e5e5;">
      </a>
    </div>
    <div style="margin-top: 12px;">
      <a href="{b.preview_url}" style="display: inline-block; margin-right: 12px; color: #1a5fb4; font: 500 14px -apple-system, sans-serif; text-decoration: none;">📱 Open feed preview</a>
      <a href="{b.image_url}" style="display: inline-block; color: #1a5fb4; font: 500 14px -apple-system, sans-serif; text-decoration: none;">🖼 Raw post image</a>
    </div>
    <div style="margin-top: 16px; padding: 16px; background: #f9f9f7; border-radius: 6px; font: 400 14px/1.55 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #2c2c2c; white-space: pre-wrap;">{caption_preview}</div>
  </td>
</tr>
""")

    posts_table = "\n".join(rows)
    failures_block = _render_failures_html(failures)
    if briefs:
        intro_body = (
            f"{intro_subject} <strong>pre-scheduled</strong> on Facebook and Instagram.<br>"
            f"If you want to nix any of them, open any Claude session and say <em>\"kill the [date] post\"</em> or reference the Post ID.<br>"
            f"No reply needed otherwise — they ship automatically."
        )
    else:
        intro_body = (
            f"{intro_subject} See the issues block below for what went wrong this run. "
            "Next Sunday's agent run will try again."
        )
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background: #fafafa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <table width="100%" cellspacing="0" cellpadding="0" border="0" style="background: #fafafa; padding: 32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellspacing="0" cellpadding="0" border="0" style="background: #ffffff; border-radius: 8px; padding: 32px; max-width: 600px;">
          <tr>
            <td>
              <div style="font: 700 24px/1.2 -apple-system, sans-serif; color: #1c1c1c;">📅 Short Go — this week's scheduled posts</div>
              <div style="font: 400 14px/1.5 -apple-system, sans-serif; color: #6a6a6a; margin-top: 6px;">
                {intro_body}
              </div>
            </td>
          </tr>
          {failures_block}
          {posts_table}
          <tr>
            <td style="padding-top: 24px; font: 400 12px/1.4 -apple-system, sans-serif; color: #9a9a9a;">
              Short Go Equine Chiropractic · Generated by Short Go Agent · Direct-to-Meta posting (no middleware)
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_weekly_email_text(briefs: Iterable[PostBrief], failures: list[dict] | None = None) -> str:
    """Plain-text fallback."""
    briefs = list(briefs)
    failures = failures or []
    lines = ["Short Go — this week's scheduled posts", "=" * 50, ""]
    if briefs:
        intro_subject = "The post below is" if len(briefs) == 1 else f"The {len(briefs)} posts below are"
        lines.append(f"{intro_subject} pre-scheduled on Facebook and Instagram.")
        lines.append("To nix any, open any Claude session and reference the Post ID.")
    else:
        lines.append("No posts were scheduled this run.")
        lines.append("See the issues list below for what went wrong; next Sunday agent run will try again.")
    lines.extend(_render_failures_text(failures))
    lines.append("")
    for i, b in enumerate(briefs, start=1):
        lines.extend([
            f"Post {i} — {b.post_type}: {b.hook}",
            f"Scheduled: {b.scheduled_at_human}",
            f"Post ID:   {b.post_id}",
            f"Image:     {b.image_url}",
            f"Preview:   {b.preview_url}",
            "",
            "Caption:",
            b.caption.strip(),
            "",
            "-" * 50,
            "",
        ])
    return "\n".join(lines)


def send_weekly_recap(
    briefs: Iterable[PostBrief],
    creds: NotifyCreds | None = None,
    failures: list[dict] | None = None,
) -> dict:
    """End-to-end: render + send the weekly recap email.

    Sends even when briefs is empty IF failures is non-empty, so a fully-failed
    run still surfaces visibly in the recap email.
    """
    briefs = list(briefs)
    failures = failures or []
    if not briefs and not failures:
        return {"skipped": True, "reason": "no posts and no failures to recap"}
    html = render_weekly_email_html(briefs, failures=failures)
    text = render_weekly_email_text(briefs, failures=failures)
    if briefs:
        noun = "post" if len(briefs) == 1 else "posts"
        if failures:
            subject = f"📅 Short Go — {len(briefs)} {noun} scheduled · {len(failures)} issue{'s' if len(failures) != 1 else ''}"
        else:
            subject = f"📅 Short Go — {len(briefs)} {noun} scheduled for this week"
    else:
        subject = f"⚠️ Short Go — Sunday run had {len(failures)} issue{'s' if len(failures) != 1 else ''}, 0 posts scheduled"
    return send_resend_email(subject=subject, html=html, text=text, creds=creds)


# ---------------------------------------------------------------------------
# Build briefs from pending-approval JSON files
# ---------------------------------------------------------------------------

# Candidate state directory locations (first existing one wins).
_STATE_CANDIDATES = [
    Path.home() / "Documents/Claude/Projects/ShortGoChiro/short-go-agent-state/pending-approval",
    Path("/sessions/zealous-lucid-bardeen/mnt/ShortGoChiro/short-go-agent-state/pending-approval"),
]


def _pending_dir() -> Path:
    for p in _STATE_CANDIDATES:
        try:
            if p.exists():
                return p
        except (PermissionError, OSError):
            # Some candidates live in other Claude sessions' mounts; skip them.
            continue
    # Fall back to the first candidate even if it doesn't exist yet — caller will see
    # an empty briefs list and skip the send.
    return _STATE_CANDIDATES[0]


def _format_scheduled_at_human(iso: str) -> str:
    """e.g. '2026-05-20T14:00:00-05:00' -> 'Wed May 20, 2:00 PM CDT'."""
    from datetime import datetime
    dt = datetime.fromisoformat(iso)
    # Try to pull a tz abbreviation; fall back to UTC offset if unavailable.
    try:
        tz_abbr = dt.strftime("%Z") or "CT"
    except Exception:
        tz_abbr = "CT"
    if not tz_abbr or tz_abbr == "":
        tz_abbr = "CT"
    # %-I is non-portable on some systems; lstrip("0") instead for the hour.
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%a %b ')}{dt.strftime('%-d') if hasattr(dt, 'strftime') else dt.day}, {hour}:{dt.strftime('%M %p')} {tz_abbr}".replace(" ", " ")


def _format_scheduled_human_simple(iso: str) -> str:
    """Robust formatter: 'Wed May 20, 2:00 PM CDT'."""
    from datetime import datetime
    dt = datetime.fromisoformat(iso)
    day = dt.day
    hour_24 = dt.hour
    hour_12 = hour_24 % 12 or 12
    ampm = "AM" if hour_24 < 12 else "PM"
    minute = f"{dt.minute:02d}"
    # Prefer a tz name; if %Z returns the offset string (Python's default for
    # fixed-offset datetimes), infer the US tz abbreviation from the offset.
    tz_label = dt.strftime("%Z") or ""
    offset = dt.utcoffset()
    looks_like_offset = tz_label.startswith("UTC") or tz_label.startswith("+") or tz_label.startswith("-") or not tz_label
    if looks_like_offset and offset is not None:
        hours = int(offset.total_seconds() // 3600)
        # Map common US offsets — Short Go ops in Central Time so CDT/CST are the
        # ones that matter; the others are here for safety.
        tz_label = {
            -5: "CDT", -6: "CST", -4: "EDT", -7: "MDT", -8: "PDT",
        }.get(hours, f"UTC{hours:+d}")
    elif not tz_label:
        tz_label = "CT"
    return f"{dt.strftime('%a %b')} {day}, {hour_12}:{minute} {ampm} {tz_label}"


def brief_from_pending_json(data: dict) -> PostBrief:
    """Build a PostBrief from one pending-approval JSON record."""
    return PostBrief(
        post_id=data["post_id"],
        post_type=(data.get("post_type") or "post").title(),
        hook=data.get("hook") or "",
        caption=data.get("caption") or "",
        image_url=data.get("public_url") or data.get("webhook_payload", {}).get("image_url", ""),
        preview_url=data.get("preview_url") or data.get("public_url") or "",
        scheduled_at_human=_format_scheduled_human_simple(data["scheduled_at"]),
        ig_status="scheduled" if data.get("meta_responses", {}).get("instagram", {}).get("scheduled") else "deferred",
    )


def load_approved_briefs(
    pending_dir: Path | None = None,
    limit: int = 10,
) -> list[PostBrief]:
    """Scan PENDING_DIR for status=='approved' posts and return PostBriefs.

    Sorted by scheduled_at ascending. Skips records missing required fields.
    """
    if pending_dir is None:
        pending_dir = _pending_dir()
    if not pending_dir.exists():
        return []

    records: list[dict] = []
    for path in sorted(pending_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("status") != "approved":
            continue
        if not data.get("scheduled_at") or not data.get("post_id"):
            continue
        records.append(data)

    # Sort by scheduled_at ascending and trim
    from datetime import datetime
    records.sort(key=lambda d: datetime.fromisoformat(d["scheduled_at"]))
    records = records[:limit]

    briefs: list[PostBrief] = []
    for r in records:
        try:
            briefs.append(brief_from_pending_json(r))
        except Exception:
            continue
    return briefs


def _failures_path(pending_dir: Path) -> Path:
    """Sibling file of pending-approval/ where the agent drops this run's failures."""
    return pending_dir.parent / "this-run-failures.json"


def load_run_failures(pending_dir: Path | None = None) -> list[dict]:
    """Read this run's failures file if it exists. Returns [] if missing/invalid."""
    if pending_dir is None:
        pending_dir = _pending_dir()
    fp = _failures_path(pending_dir)
    try:
        if not fp.exists():
            return []
        data = json.loads(fp.read_text())
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        return []
    except (OSError, json.JSONDecodeError):
        return []


def clear_run_failures(pending_dir: Path | None = None) -> None:
    """Delete this run's failures file after a successful email send."""
    if pending_dir is None:
        pending_dir = _pending_dir()
    fp = _failures_path(pending_dir)
    try:
        if fp.exists():
            fp.unlink()
    except OSError:
        pass


def send_weekly_recap_from_pending(
    pending_dir: Path | None = None,
    limit: int = 10,
    creds: NotifyCreds | None = None,
    failures: list[dict] | None = None,
) -> dict:
    """Build briefs from approved posts in PENDING_DIR and send the recap email.

    Failures from this run are picked up from either:
      (a) the `failures` argument (explicit pass-through), OR
      (b) the sibling file `<state>/this-run-failures.json` if (a) is None.

    On a successful send, the failures file is deleted so it doesn't bleed into
    the next run.
    """
    if pending_dir is None:
        pending_dir = _pending_dir()
    briefs = load_approved_briefs(pending_dir=pending_dir, limit=limit)
    if failures is None:
        failures = load_run_failures(pending_dir=pending_dir)
    if not briefs and not failures:
        return {"skipped": True, "reason": "no approved posts and no failures to recap"}
    result = send_weekly_recap(briefs, creds=creds, failures=failures)
    # Only clear the failures file if Resend accepted the email (i.e., we got an id back).
    if result.get("id"):
        clear_run_failures(pending_dir=pending_dir)
    return result


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Weekly recap email sender.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_test = sub.add_parser("test", help="Send a hardcoded sample weekly recap.")
    p_send = sub.add_parser("send-from-pending", help="Build briefs from pending-approval files and send.")
    p_send.add_argument("--limit", type=int, default=3)

    args = p.parse_args()

    if args.cmd == "test":
        sample = [
            PostBrief(
                post_id="2026-05-15-edu-test-clean",
                post_type="Educational",
                hook="WATCH HIS THIRD BARREL.",
                caption=(
                    "You see it on the run-out. Drifts wide, lead's late, lands off behind into the alley.\n\n"
                    "Mobile chiropractic across DFW and North Texas. DM 'BOOK' for the next open spot."
                ),
                image_url="https://raw.githubusercontent.com/shortgoequinechiro-rgb/shortgo-marketing-assets/main/posts/2026-05-22/2026-05-15-edu-test-clean.png",
                preview_url="https://raw.githubusercontent.com/shortgoequinechiro-rgb/shortgo-marketing-assets/main/previews/2026-05-22/2026-05-15-edu-test-clean.preview.png",
                scheduled_at_human="Wed May 20, 2:00 PM CDT",
                ig_status="deferred",
            ),
        ]
        print(json.dumps(send_weekly_recap(sample), indent=2))
    elif args.cmd == "send-from-pending":
        result = send_weekly_recap_from_pending(limit=args.limit)
        print(json.dumps(result, indent=2))
