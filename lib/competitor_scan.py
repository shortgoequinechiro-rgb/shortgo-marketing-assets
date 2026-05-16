"""
Competitor landscape scan.

Once a week (cached for 7 days), this module asks Claude — with the web_search
tool enabled — to discover what OTHER equine chiropractors and mobile equine
performance pros across TX/MT/the rodeo circuit have been posting. It returns
a structured brief that the Sunday drafting prompt uses to **avoid** copying:

  - recent_topics:           themes the competitor field is hammering
  - common_hook_patterns:    recurring opening-line structures
  - dominant_visual_styles:  photo types saturating the feed

The point is differentiation. Short Go's content should not look or sound like
everyone else's.

Cost notes
----------
Each scan = 1 Claude API call with the web_search tool (~$0.05–$0.15 typical).
The result is cached at state/competitor-scan.json with a 7-day TTL, so a
freshly-cached scan reuses (zero new spend) on a re-run.

API surface
-----------
- scan_competitor_landscape(api_key, force=False, ...)  →  CompetitorBrief
- load_cached(path=...)                                  →  CompetitorBrief | None
- format_avoid_section(brief)                            →  str (drop-in for prompt)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()


def _repo_root() -> Path:
    """Match image_host's resolution: $GITHUB_WORKSPACE → ancestor walk → fallback."""
    gha = os.environ.get("GITHUB_WORKSPACE")
    if gha and (Path(gha) / ".git").exists():
        return Path(gha)
    here = _THIS.parent
    for ancestor in [here, *here.parents]:
        if (ancestor / ".git").exists() and (ancestor / "lib").exists():
            return ancestor
    return _THIS.parent.parent  # last resort


CACHE_PATH = _repo_root() / "state" / "competitor-scan.json"
CACHE_TTL = timedelta(days=7)


@dataclass
class CompetitorBrief:
    scanned_at: str  # ISO timestamp
    recent_topics: list[str] = field(default_factory=list)
    common_hook_patterns: list[str] = field(default_factory=list)
    dominant_visual_styles: list[str] = field(default_factory=list)
    raw_summary: str = ""  # Claude's narrative for debugging / inspection
    sources: list[str] = field(default_factory=list)  # citations Claude pulled

    @property
    def age(self) -> timedelta:
        try:
            return datetime.now(timezone.utc) - datetime.fromisoformat(self.scanned_at)
        except Exception:
            return CACHE_TTL + timedelta(days=1)  # treat as stale

    def is_fresh(self, ttl: timedelta = CACHE_TTL) -> bool:
        return self.age < ttl


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def load_cached(path: Path = CACHE_PATH) -> CompetitorBrief | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return CompetitorBrief(**data)
    except Exception:
        return None


def save_cache(brief: CompetitorBrief, path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(brief), indent=2))


# ---------------------------------------------------------------------------
# Anthropic API (web_search-enabled)
# ---------------------------------------------------------------------------

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a competitive intelligence analyst for an equine chiropractic business.

The business is Short Go Equine Chiropractic — Dr. Leo runs a mobile (barn-call) practice across DFW/North Texas and Montana, working with rodeo, ranch, and performance horse owners. Their voice is rural-luxury, no-fluff, hauler-and-rider literate.

Your job: scan what OTHER equine chiropractors, equine bodyworkers, and mobile equine performance pros across the rodeo circuit have been posting on Instagram and Facebook recently. Look for:

  1. Recurring TOPICS (e.g., "hock arthritis," "post-haul recovery," "saddle fit," "TMJ release")
  2. Common HOOK PATTERNS (e.g., "3 signs your horse is...," "It's not the bit, it's...," "Did you know...")
  3. Dominant VISUAL STYLES (e.g., "clinical close-up of adjustment," "before/after gait video," "horse-and-rider portrait at sunset," "anatomy diagram overlay")

Return EXACTLY one JSON object with these keys:

{
  "recent_topics":          ["topic 1", "topic 2", ...],   // 5-10 entries
  "common_hook_patterns":   ["pattern 1", ...],            // 4-8 entries
  "dominant_visual_styles": ["style 1", ...],              // 3-6 entries
  "raw_summary":            "one paragraph synthesis",
  "sources":                ["https://...", ...]           // URLs you cited
}

Important: do NOT include posts from Short Go Equine Chiropractic itself, shortgochiro.com, or @shortgoequinechiro. Focus on competitors and adjacent practitioners.

If your web searches return nothing meaningful, return the JSON with empty arrays and a raw_summary explaining what you tried."""

USER_PROMPT = """Use web_search to find what equine chiropractors and equine performance pros have been posting in the last 30–60 days. Run multiple searches if needed. Suggested queries:

  - "equine chiropractor instagram post 2026"
  - "mobile equine chiropractor Texas Facebook"
  - "horse adjustment before after"
  - "rodeo horse chiropractor"
  - "equine bodywork Texas Montana"

Then synthesize what you found into the JSON structure described in your system prompt. Be concrete — name actual themes you see, not generic categories. Cite URLs."""


def _post(url: str, body: dict, headers: dict, timeout: int = 90) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic HTTP {e.code}: {err}") from None


def _call_claude_with_web_search(api_key: str) -> dict:
    """Single API call with web_search tool enabled. Returns parsed JSON brief."""
    body = {
        "model": MODEL,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "tools": [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 6}
        ],
        "messages": [
            {"role": "user", "content": USER_PROMPT},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    resp = _post(ANTHROPIC_URL, body, headers)
    # Pull text out of the assistant message (skipping tool_use / tool_result blocks)
    text_chunks: list[str] = []
    for block in resp.get("content", []):
        if block.get("type") == "text":
            text_chunks.append(block.get("text", ""))
    text = "\n".join(text_chunks).strip()
    # Find the first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"No JSON object found in response: {text[:300]}...")
    return json.loads(text[start : end + 1])


def scan_competitor_landscape(
    api_key: str | None = None,
    *,
    force: bool = False,
    cache_path: Path = CACHE_PATH,
    cache_ttl: timedelta = CACHE_TTL,
) -> CompetitorBrief:
    """Return a competitor brief, hitting the API only when the cache is stale or missing.

    Set force=True to bypass the cache and re-scan immediately.
    """
    if not force:
        cached = load_cached(cache_path)
        if cached and cached.is_fresh(cache_ttl):
            return cached

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY required for competitor scan")

    payload = _call_claude_with_web_search(api_key)
    brief = CompetitorBrief(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        recent_topics=list(payload.get("recent_topics", [])),
        common_hook_patterns=list(payload.get("common_hook_patterns", [])),
        dominant_visual_styles=list(payload.get("dominant_visual_styles", [])),
        raw_summary=str(payload.get("raw_summary", "")),
        sources=list(payload.get("sources", [])),
    )
    save_cache(brief, cache_path)
    return brief


# ---------------------------------------------------------------------------
# Prompt integration
# ---------------------------------------------------------------------------

def format_avoid_section(brief: CompetitorBrief | None) -> str:
    """Drop-in 'AVOID' block for the Sunday drafting prompt.

    Returns empty string if no brief, so the prompt degrades gracefully.
    """
    if not brief:
        return ""
    parts: list[str] = [
        "",
        "## DIFFERENTIATION RULES — what other practitioners are saturating right now",
        "Avoid sounding like the herd. Specifically, this week:",
    ]
    if brief.recent_topics:
        topics = ", ".join(brief.recent_topics[:8])
        parts.append(f"- **TOPIC dedupe**: don't repeat topics that the field is already hammering — {topics}.")
    if brief.common_hook_patterns:
        hooks = "; ".join(f'"{h}"' for h in brief.common_hook_patterns[:6])
        parts.append(f"- **HOOK angle dedupe**: don't open with patterns that are everywhere right now — {hooks}.")
    if brief.dominant_visual_styles:
        styles = ", ".join(brief.dominant_visual_styles[:6])
        parts.append(f"- **VISUAL style dedupe**: avoid the same photo treatments that dominate the field — {styles}. Lean into Short Go's rural-luxury arena/landscape/saddle vibe instead.")
    parts.append("")
    parts.append("If a strong post idea genuinely overlaps with one of these — say so in a note, then propose an angle that re-frames it as distinctly Short Go.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Competitor landscape scan (cached).")
    p.add_argument("--force", action="store_true", help="Bypass cache and re-scan now.")
    p.add_argument("--show", action="store_true", help="Print the cached brief without scanning.")
    p.add_argument("--format-prompt", action="store_true", help="Print just the AVOID prompt section.")
    args = p.parse_args()

    if args.show:
        brief = load_cached()
        if not brief:
            print("(no cache present)")
            return 1
        print(json.dumps(asdict(brief), indent=2))
        return 0

    if args.format_prompt:
        brief = load_cached()
        print(format_avoid_section(brief))
        return 0

    brief = scan_competitor_landscape(force=args.force)
    print(json.dumps(asdict(brief), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
