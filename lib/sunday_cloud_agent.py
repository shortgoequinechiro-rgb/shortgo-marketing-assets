"""
Short Go agent — cloud-hosted Sunday run.

Orchestrates the full weekly post pipeline. Designed to run unattended in
GitHub Actions (or any cloud cron):

    1. Load brand context (business-context.md) from this repo
    2. Read content-log.md to know what was posted/rejected before
    3. List backgrounds in photo-library/
    4. Invoke Claude API to draft 3 posts for the week
    5. Render each post via compose_post.py
    6. Commit rendered images to the repo at posts/YYYY-MM-DD/{post_id}.png
    7. Schedule FB posts via Meta Graph API (native scheduling)
    8. Save IG posts to state/pending-ig.json for hourly cron to fire
    9. Send weekly recap email via Resend
    10. Append PROPOSED entries to content-log.md
    11. git push state changes

All credentials come from environment variables (set as GitHub Secrets):
    ANTHROPIC_API_KEY     - for drafting
    META_PAGE_ACCESS_TOKEN, META_PAGE_ID, META_IG_USER_ID - for posting
    RESEND_API_KEY, NOTIFY_TO_EMAIL, NOTIFY_FROM_EMAIL, NOTIFY_FROM_NAME
    GITHUB_OUTPUT_REPO_OWNER, GITHUB_OUTPUT_REPO_NAME - where images get committed

Local-Mac mode (for testing): if env vars are missing, falls back to reading
from ~/.config/short-go-agent/*.txt files.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Bring the lib path into scope when run directly
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))

# ---------------------------------------------------------------------------
# Credentials loader — env first, then disk
# ---------------------------------------------------------------------------

def _env_or_file(env_key: str, file_path: Path, file_key: str | None = None) -> str | None:
    """Prefer env var; fall back to a key=value line in a config file."""
    v = os.environ.get(env_key)
    if v:
        return v
    if file_path.exists():
        for line in file_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, val = line.split("=", 1)
                if k.strip() == (file_key or env_key):
                    return val.strip()
    return None


def load_all_secrets() -> dict:
    cfg = Path.home() / ".config/short-go-agent"
    meta_creds = cfg / "meta-credentials"
    notify = cfg / "notify.txt"
    return {
        "anthropic_api_key": _env_or_file("ANTHROPIC_API_KEY", notify),
        "meta_page_token":   _env_or_file("META_PAGE_ACCESS_TOKEN", meta_creds, "PAGE_ACCESS_TOKEN"),
        "meta_page_id":      _env_or_file("META_PAGE_ID", meta_creds, "PAGE_ID"),
        "meta_ig_user_id":   _env_or_file("META_IG_USER_ID", meta_creds, "IG_USER_ID"),
        "resend_api_key":    _env_or_file("RESEND_API_KEY", notify),
        "notify_to":         _env_or_file("NOTIFY_TO_EMAIL", notify) or "shortgoequinechiro@gmail.com",
        "notify_from":       _env_or_file("NOTIFY_FROM_EMAIL", notify) or "hello@chirostride.com",
        "notify_from_name":  _env_or_file("NOTIFY_FROM_NAME", notify) or "Short Go Agent",
    }


# ---------------------------------------------------------------------------
# Claude API — draft 3 posts for the week
# ---------------------------------------------------------------------------

def draft_weekly_posts(
    business_context: str,
    content_log: str,
    available_backgrounds: list[str],
    api_key: str,
) -> list[dict]:
    """Call Claude API to draft 3 posts for the coming week.

    Returns a list of 3 dicts, each shaped like:
        {
            "post_id": "2026-05-25-edu-001",
            "post_type": "educational",   # or "cta" | "quote"
            "background_filename": "008-barn-aisle-light-beam.png",
            "hook": ["WATCH HIS", "THIRD BARREL."],
            "body": ["...", "...", "..."],
            "explainer": "...",
            "caption": "...",
            "scheduled_at": "2026-05-27T14:00:00-05:00",
        }
    """
    import urllib.request
    import urllib.error

    # Compute the 3 publish targets: Wed + Fri + Sun this coming week (Chicago tz, 2pm)
    tz = ZoneInfo("America/Chicago")
    today = datetime.now(tz)
    days_until_wed = (2 - today.weekday()) % 7
    if days_until_wed == 0 and today.hour >= 14:
        days_until_wed = 7
    wed = (today + timedelta(days=days_until_wed)).replace(hour=14, minute=0, second=0, microsecond=0)
    fri = wed + timedelta(days=2)
    sun = wed + timedelta(days=4)
    targets = [wed.isoformat(), fri.isoformat(), sun.isoformat()]

    prompt = f"""You are the Short Go Equine Chiropractic static-post agent. Draft exactly 3 static IG/FB posts for the coming week, scheduled for Wed/Fri/Sun afternoon.

BRAND CONTEXT (source of truth — voice, audience, rules):
{business_context}

RECENT CONTENT LOG (don't repeat angles from last 4 weeks; learn from REJECTED entries):
{content_log[:6000]}

AVAILABLE BACKGROUND FILENAMES (must pick from this list — these are pre-generated brand-locked images):
{json.dumps(available_backgrounds, indent=2)}

SCHEDULE TARGETS (in order):
- Wed: {targets[0]}
- Fri: {targets[1]}
- Sun: {targets[2]}

OUTPUT: respond with ONLY a JSON array of 3 post objects (no prose, no markdown fence). Each object:
{{
  "post_id":  "<YYYY-MM-DD>-<type>-<short-slug>",     e.g. "2026-05-27-edu-third-barrel"
  "post_type": "educational" | "cta" | "quote",
  "background_filename": "<must be one of AVAILABLE BACKGROUND FILENAMES>",
  "hook":     ["LINE 1", "LINE 2"],                   1-3 uppercase lines, editorial cadence
  "body":     ["...", "...", "..."],                  for educational: 3 symptom bullets; for cta: 1-2 where/when lines; for quote: []
  "explainer": "<one-line value statement, under 90 chars>",     optional for quote
  "caption":  "<50-180 words, brand voice, ends with locked CTA>",
  "scheduled_at": "<one of the 3 schedule targets above>"
}}

HARD CONSTRAINTS (non-negotiable):
- NEVER name a specific city/barn/event/weekend unless business_context confirms Drew is scheduled there. Use generic regional language ("DFW," "North Texas," "Great Falls area") freely.
- Every CTA must end with a DM/BOOK or "Book now" variant
- TX or MT geographic cue in every caption
- Plain rider voice — never clinical jargon
- Attribute clinical work to Dr. Leo, never Charles
- 70% value / 20% proof / 10% direct sell mix across the 3 posts
- Don't repeat the SAME background within the 3 posts of this week

Respond with JSON only."""

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "ShortGoAgent/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic HTTP {e.code}: {err}") from None

    text = "".join(c.get("text", "") for c in payload.get("content", []))
    # Strip code fence if Claude wrapped the JSON despite instructions
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Main Sunday loop
# ---------------------------------------------------------------------------

def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent  # Marketing Agent/
    secrets = load_all_secrets()
    missing = [k for k, v in secrets.items() if not v and k not in ("meta_ig_user_id",)]
    if missing:
        print(f"FATAL: missing secrets: {missing}", file=sys.stderr)
        return 2

    bc_path = repo_root / "business-context.md"
    cl_path = repo_root / "content-log.md"
    bg_dir = repo_root / "photo-library"
    if not (bc_path.exists() and cl_path.exists() and bg_dir.exists()):
        print(f"FATAL: required files missing under {repo_root}", file=sys.stderr)
        return 2

    business_context = bc_path.read_text()
    content_log = cl_path.read_text()
    backgrounds = sorted([
        p.name for p in bg_dir.glob("*.png")
        if not p.name.startswith("_") and p.name != "README.md"
    ])
    print(f"Loaded context. {len(backgrounds)} backgrounds available.")

    # 1. Draft 3 posts via Claude API
    print("Drafting 3 posts via Claude API...")
    plans = draft_weekly_posts(business_context, content_log, backgrounds, secrets["anthropic_api_key"])
    print(f"  Drafted {len(plans)} posts:")
    for p in plans:
        print(f"    - {p['post_id']} ({p['post_type']}) — {' '.join(p['hook'])[:60]}")

    # 2. Render + stage each post
    from compose_post import compose_post, PostSpec
    from feed_preview import render_feed_preview
    from image_host import stage_image, raw_url_for_key, build_key, repo_root as assets_repo

    render_dir = repo_root / "_renders"
    render_dir.mkdir(exist_ok=True)
    prepared: list[dict] = []

    for plan in plans:
        bg_path = bg_dir / plan["background_filename"]
        if not bg_path.exists():
            print(f"  SKIP {plan['post_id']}: background {plan['background_filename']} not found")
            continue

        # Render the post PNG
        render_path = render_dir / f"{plan['post_id']}.png"
        spec = PostSpec(
            background_path=bg_path,
            template=plan["post_type"],
            hook=plan["hook"],
            body=plan.get("body", []),
            explainer=plan.get("explainer"),
        )
        compose_post(spec, render_path)

        # Render feed preview
        preview_render = render_dir / f"{plan['post_id']}.preview.png"
        render_feed_preview(render_path, plan["caption"], preview_render)

        # Stage both into the assets repo clone
        post_key = build_key(plan["post_id"], plan["scheduled_at"])
        preview_key = post_key.replace("posts/", "previews/", 1).replace(".png", ".preview.png")
        stage_image(render_path, post_key)
        stage_image(preview_render, preview_key)

        prepared.append({
            **plan,
            "image_url": raw_url_for_key(post_key),
            "preview_url": raw_url_for_key(preview_key),
        })
        print(f"  Staged {plan['post_id']}")

    # 3. (In cloud env, git push happens via the calling GitHub Action;
    #     in local-Mac env, the user does it via osascript)
    print()
    print("Staged images committed; rely on host runner to git push.")

    # 4. Schedule FB posts + defer IG
    from post_to_meta import load_credentials as load_meta, schedule_facebook_post, MetaCreds
    meta_creds = MetaCreds(
        page_id=secrets["meta_page_id"],
        page_name="Short Go Equine Chiropractic",
        page_access_token=secrets["meta_page_token"],
        ig_user_id=secrets["meta_ig_user_id"],
    )

    pending_ig: list[dict] = []
    for post in prepared:
        try:
            fb = schedule_facebook_post(post["image_url"], post["caption"], post["scheduled_at"], creds=meta_creds)
            post["fb_post_id"] = fb["post_id"]
            print(f"  FB scheduled {post['post_id']} → fb_post_id={fb['post_id']}")
        except Exception as e:
            post["fb_error"] = str(e)
            print(f"  FB FAILED {post['post_id']}: {e}")

        if meta_creds.ig_user_id:
            pending_ig.append({
                "post_id": post["post_id"],
                "scheduled_at": post["scheduled_at"],
                "image_url": post["image_url"],
                "caption": post["caption"],
            })

    # Persist IG defer queue (cloud env writes to repo-relative state/)
    state_dir = repo_root / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "pending-ig.json").write_text(json.dumps(pending_ig, indent=2))
    print(f"Saved {len(pending_ig)} IG defers to state/pending-ig.json")

    # 5. Email recap
    from notify_weekly_email import PostBrief, send_weekly_recap, NotifyCreds
    tz_chi = ZoneInfo("America/Chicago")
    briefs = [
        PostBrief(
            post_id=p["post_id"],
            post_type=p["post_type"].title(),
            hook=" ".join(p["hook"]),
            caption=p["caption"],
            image_url=p["image_url"],
            preview_url=p["preview_url"],
            scheduled_at_human=datetime.fromisoformat(p["scheduled_at"]).astimezone(tz_chi).strftime("%a %b %-d, %-I:%M %p %Z"),
            fb_post_id=p.get("fb_post_id"),
            ig_status="deferred" if meta_creds.ig_user_id else "skipped",
        )
        for p in prepared
    ]
    notify_creds = NotifyCreds(
        resend_api_key=secrets["resend_api_key"],
        to_email=secrets["notify_to"],
        from_email=secrets["notify_from"],
        from_name=secrets["notify_from_name"],
    )
    email_resp = send_weekly_recap(briefs, creds=notify_creds)
    print(f"Recap email sent: {email_resp}")

    # 6. Append PROPOSED entries to content-log
    today_str = datetime.now(tz_chi).date().isoformat()
    log_lines = []
    for p in prepared:
        hook = " ".join(p["hook"])[:80]
        log_lines.append(f"{today_str} | Static | {p['post_type']} | {hook} | {p['scheduled_at']}")
    log_block = "\n".join(log_lines)
    existing = cl_path.read_text()
    if "## PROPOSED" in existing:
        existing = existing.replace("## PROPOSED\n", "## PROPOSED\n" + log_block + "\n", 1)
    else:
        existing = existing.rstrip() + "\n\n## PROPOSED\n" + log_block + "\n"
    cl_path.write_text(existing)

    print("Sunday run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
