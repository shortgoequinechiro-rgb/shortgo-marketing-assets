"""
Short Go agent — direct Meta Graph API posting.

Replaces the Make.com + Buffer middleware. Posts directly to:
- Facebook Page (Short Go Equine Chiropractic) via Page Access Token
- Instagram Business (when IG_USER_ID becomes available)

Credentials live at ~/.config/short-go-agent/meta-credentials with chmod 600:
    PAGE_ID=...
    PAGE_NAME=...
    PAGE_ACCESS_TOKEN=...
    IG_USER_ID=...   (optional — once IG is wired up)

The Page Access Token is permanent (doesn't expire) as long as Charles
remains an admin of the Page and doesn't change his Facebook password.

Architecture:
- Facebook supports native scheduling via the API. We pass
  `scheduled_publish_time` and Meta handles publishing at that time.
  No agent-side cron needed for FB.
- Instagram does NOT natively schedule image feed posts via the Graph API.
  The agent's existing scheduled task fires at the publish time and calls
  post_to_instagram() to publish immediately.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


GRAPH = "https://graph.facebook.com/v22.0"
CREDS_PATH = Path.home() / ".config/short-go-agent/meta-credentials"


# ---------------------------------------------------------------------------
# Credentials loader
# ---------------------------------------------------------------------------

@dataclass
class MetaCreds:
    page_id: str
    page_name: str
    page_access_token: str
    ig_user_id: str | None


def load_credentials(path: Path = CREDS_PATH) -> MetaCreds:
    """Load Page Access Token + Page ID + IG User ID from local config."""
    if not path.exists():
        raise FileNotFoundError(
            f"Meta credentials not found at {path}. "
            "Run the Meta app setup flow (see Short Go agent docs)."
        )
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

    return MetaCreds(
        page_id=env["PAGE_ID"],
        page_name=env.get("PAGE_NAME", "Unknown Page"),
        page_access_token=env["PAGE_ACCESS_TOKEN"],
        ig_user_id=env.get("IG_USER_ID") or None,
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post_graph(path: str, params: dict, timeout: int = 30) -> dict:
    """POST to Meta Graph API. Returns parsed JSON or raises with the API error."""
    url = GRAPH + path
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
        except Exception:
            err = {"raw": body}
        raise MetaAPIError(f"HTTP {e.code} on POST {path}: {err}")


def _get_graph(path: str, params: dict, timeout: int = 30) -> dict:
    """GET from Meta Graph API."""
    url = GRAPH + path + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
        except Exception:
            err = {"raw": body}
        raise MetaAPIError(f"HTTP {e.code} on GET {path}: {err}")


class MetaAPIError(Exception):
    pass


# ---------------------------------------------------------------------------
# Facebook Page posting
# ---------------------------------------------------------------------------

def schedule_facebook_post(
    image_url: str,
    caption: str,
    scheduled_at: datetime | str,
    creds: MetaCreds | None = None,
) -> dict:
    """Schedule a photo post on the Facebook Page.

    Args:
        image_url:    Publicly accessible URL of the post image (raw.githubusercontent.com OK)
        caption:      Post body text
        scheduled_at: When to publish. datetime (any tz) or ISO string.
                      Must be at least 10 minutes in the future and at most 6 months out.
        creds:        Optional pre-loaded credentials; loads from disk if None.

    Returns:
        Dict with the scheduled post's id, plus a confirmation field.

    The Facebook side handles scheduling natively — no agent cron needed.
    """
    if creds is None:
        creds = load_credentials()

    if isinstance(scheduled_at, str):
        scheduled_at = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    unix_ts = int(scheduled_at.timestamp())

    # Validation: Meta requires 10 min < delta < 6 months
    now = datetime.now(timezone.utc).timestamp()
    delta = unix_ts - now
    if delta < 600:
        raise ValueError(
            f"scheduled_at must be at least 10 minutes in the future. "
            f"Got delta={delta:.0f}s"
        )
    if delta > 60 * 60 * 24 * 30 * 6:
        raise ValueError("scheduled_at cannot be more than 6 months out.")

    # Two-step scheduling so the post shows in Meta Business Suite Planner.
    #
    # Single-step /photos scheduling DOES schedule a real post, but MBS's
    # Planner UI silently excludes those — Charles couldn't see them and we
    # had to dig via API to verify. The /feed path with attached_media surfaces
    # the post in MBS like a manually-scheduled one.
    #
    # Step 1: upload the photo as unpublished media. No scheduled_publish_time
    # here — that goes on the feed post in step 2. We get back a media_fbid.
    upload = _post_graph(f"/{creds.page_id}/photos", {
        "url": image_url,
        "published": "false",
        "access_token": creds.page_access_token,
    })
    media_fbid = upload.get("id")
    if not media_fbid:
        raise MetaAPIError(f"Photo upload returned no id: {upload}")

    # Step 2: schedule the feed post that references the uploaded photo.
    feed = _post_graph(f"/{creds.page_id}/feed", {
        "message": caption,
        "attached_media": json.dumps([{"media_fbid": media_fbid}]),
        "published": "false",
        "scheduled_publish_time": str(unix_ts),
        "access_token": creds.page_access_token,
    })
    return {
        "platform": "facebook",
        "page_id": creds.page_id,
        "page_name": creds.page_name,
        "post_id": feed.get("id") or feed.get("post_id"),
        "media_fbid": media_fbid,
        "scheduled_for_unix": unix_ts,
        "scheduled_for_iso": scheduled_at.isoformat(),
        "raw_response": feed,
        "upload_response": upload,
    }


def post_facebook_now(
    image_url: str,
    caption: str,
    creds: MetaCreds | None = None,
) -> dict:
    """Publish to the Facebook Page immediately (no scheduling)."""
    if creds is None:
        creds = load_credentials()
    resp = _post_graph(f"/{creds.page_id}/photos", {
        "url": image_url,
        "caption": caption,
        "published": "true",
        "access_token": creds.page_access_token,
    })
    return {
        "platform": "facebook",
        "page_id": creds.page_id,
        "page_name": creds.page_name,
        "post_id": resp.get("id") or resp.get("post_id"),
        "raw_response": resp,
    }


def list_scheduled_facebook_posts(creds: MetaCreds | None = None) -> list[dict]:
    """Return the currently-scheduled (un-published) Page posts.

    Useful for the smoke test + the weekly review.
    """
    if creds is None:
        creds = load_credentials()
    resp = _get_graph(f"/{creds.page_id}/scheduled_posts", {
        "fields": "id,message,scheduled_publish_time,is_published,attachments",
        "access_token": creds.page_access_token,
    })
    return resp.get("data", [])


def delete_facebook_post(post_id: str, creds: MetaCreds | None = None) -> dict:
    """Delete a scheduled or published Page post by id."""
    if creds is None:
        creds = load_credentials()
    url = GRAPH + f"/{post_id}?access_token={urllib.parse.quote(creds.page_access_token)}"
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Instagram posting — stub for when IG_USER_ID is wired up
# ---------------------------------------------------------------------------

def post_to_instagram_now(
    image_url: str,
    caption: str,
    creds: MetaCreds | None = None,
) -> dict:
    """Publish a photo post to Instagram Business account immediately.

    Two-step Container API:
      1. Create media container with image_url + caption → returns creation_id
      2. Publish the creation_id

    IG does not natively schedule image feed posts via the Graph API.
    The agent's scheduled task fires at the publish time and calls this.
    """
    if creds is None:
        creds = load_credentials()
    if not creds.ig_user_id:
        raise MetaAPIError(
            "IG_USER_ID not set in meta-credentials. "
            "Instagram authorization is still pending — run the IG OAuth flow first."
        )

    # 1. Create media container
    container = _post_graph(f"/{creds.ig_user_id}/media", {
        "image_url": image_url,
        "caption": caption,
        "access_token": creds.page_access_token,
    })
    creation_id = container.get("id")
    if not creation_id:
        raise MetaAPIError(f"No creation_id in container response: {container}")

    # 2. Publish (image posts don't need status polling — Reels/Videos do)
    publish = _post_graph(f"/{creds.ig_user_id}/media_publish", {
        "creation_id": creation_id,
        "access_token": creds.page_access_token,
    })
    return {
        "platform": "instagram",
        "ig_user_id": creds.ig_user_id,
        "creation_id": creation_id,
        "post_id": publish.get("id"),
        "raw_response": publish,
    }


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Direct Meta Graph API posting.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_fb = sub.add_parser("schedule-fb", help="Schedule a FB Page post.")
    p_fb.add_argument("--image-url", required=True)
    p_fb.add_argument("--caption", required=True)
    p_fb.add_argument("--at", required=True, help="ISO datetime, e.g. 2026-05-15T15:30:00-05:00")

    p_fbn = sub.add_parser("fb-now", help="Publish a FB Page post immediately.")
    p_fbn.add_argument("--image-url", required=True)
    p_fbn.add_argument("--caption", required=True)

    p_list = sub.add_parser("list-scheduled", help="List scheduled FB posts.")

    p_del = sub.add_parser("delete", help="Delete a post by id.")
    p_del.add_argument("post_id")

    p_ig = sub.add_parser("ig-now", help="Publish an IG post immediately (requires IG_USER_ID).")
    p_ig.add_argument("--image-url", required=True)
    p_ig.add_argument("--caption", required=True)

    p_creds = sub.add_parser("creds", help="Show loaded credentials (token lengths only).")

    args = p.parse_args()

    if args.cmd == "creds":
        c = load_credentials()
        print(json.dumps({
            "page_id": c.page_id,
            "page_name": c.page_name,
            "page_access_token_length": len(c.page_access_token),
            "ig_user_id": c.ig_user_id,
        }, indent=2))
    elif args.cmd == "schedule-fb":
        print(json.dumps(schedule_facebook_post(args.image_url, args.caption, args.at), indent=2))
    elif args.cmd == "fb-now":
        print(json.dumps(post_facebook_now(args.image_url, args.caption), indent=2))
    elif args.cmd == "list-scheduled":
        print(json.dumps(list_scheduled_facebook_posts(), indent=2))
    elif args.cmd == "delete":
        print(json.dumps(delete_facebook_post(args.post_id), indent=2))
    elif args.cmd == "ig-now":
        print(json.dumps(post_to_instagram_now(args.image_url, args.caption), indent=2))
