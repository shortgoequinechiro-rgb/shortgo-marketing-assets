"""
Short Go agent — weekly post-metrics dump.

Reads state/scheduled-fb-index.json for published FB posts and queries the
Graph API for engagement; pulls recent IG media engagement directly from the
IG user node. Writes everything to state/post-metrics.json so the weekly
marketing agent (which has no access to Meta credentials) can read real
performance data from the public repo.

Runs in GitHub Actions (weekly-metrics.yml) with the same secrets as the
hourly IG fire job. Safe to run any time; failures on individual posts are
skipped, never fatal.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GRAPH = "https://graph.facebook.com/v22.0"
ROOT = Path(__file__).resolve().parent.parent
FB_INDEX = ROOT / "state" / "scheduled-fb-index.json"
OUT = ROOT / "state" / "post-metrics.json"

TOKEN = os.environ["META_PAGE_ACCESS_TOKEN"]
PAGE_ID = os.environ["META_PAGE_ID"]
IG_USER_ID = os.environ.get("META_IG_USER_ID", "")


def get(path: str, **params) -> dict | None:
    params["access_token"] = TOKEN
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001 — per-post failures are non-fatal
        print(f"  skip {path}: {e}", file=sys.stderr)
        return None


def fb_metrics() -> dict:
    if not FB_INDEX.exists():
        return {}
    index = json.loads(FB_INDEX.read_text())
    out = {}
    for slug, post_id in index.items():
        # Early entries were stored without the page prefix.
        full_id = post_id if "_" in post_id else f"{PAGE_ID}_{post_id}"
        data = get(
            full_id,
            fields="created_time,likes.summary(true),comments.summary(true),shares",
        )
        if not data or "created_time" not in data:
            continue  # not published yet (still scheduled) or deleted
        row = {
            "fb_post_id": full_id,
            "published_at": data.get("created_time"),
            "likes": data.get("likes", {}).get("summary", {}).get("total_count", 0),
            "comments": data.get("comments", {}).get("summary", {}).get("total_count", 0),
            "shares": data.get("shares", {}).get("count", 0),
        }
        ins = get(f"{full_id}/insights", metric="post_impressions,post_impressions_unique")
        if ins:
            for m in ins.get("data", []):
                vals = m.get("values", [{}])
                key = "impressions" if m["name"] == "post_impressions" else "reach"
                row[key] = vals[0].get("value", 0) if vals else 0
        out[slug] = row
    return out


def ig_metrics() -> list:
    if not IG_USER_ID:
        return []
    data = get(
        f"{IG_USER_ID}/media",
        fields="id,caption,timestamp,media_type,like_count,comments_count,permalink",
        limit="25",
    )
    if not data:
        return []
    out = []
    for m in data.get("data", []):
        row = {
            "ig_media_id": m["id"],
            "published_at": m.get("timestamp"),
            "caption_head": (m.get("caption") or "")[:80],
            "likes": m.get("like_count", 0),
            "comments": m.get("comments_count", 0),
            "permalink": m.get("permalink"),
        }
        ins = get(f"{m['id']}/insights", metric="reach,saved")
        if ins:
            for metric in ins.get("data", []):
                vals = metric.get("values", [{}])
                row[metric["name"]] = vals[0].get("value", 0) if vals else 0
        out.append(row)
    return out


def main() -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "facebook": fb_metrics(),
        "instagram": ig_metrics(),
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {OUT} — {len(payload['facebook'])} FB posts, {len(payload['instagram'])} IG media.")


if __name__ == "__main__":
    main()
