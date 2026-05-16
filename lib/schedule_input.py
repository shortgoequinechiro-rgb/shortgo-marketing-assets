"""
Drew's confirmed schedule input.

Reads state/drew-schedule.md and returns a list of (date, location_text) tuples
for the upcoming week. The Sunday drafting prompt uses this so CTAs can be
location-specific (e.g. "Drew is in Granbury Thursday — DM to book") instead
of generic regional language.

Format (state/drew-schedule.md):

    ## 2026-05-20  Granbury, TX  (multiple barrels)
    ## 2026-05-21  Aubrey, TX
    ## 2026-05-22  DFW (open — booking up)
    ## 2026-05-24  Decatur, TX

If the file is missing or has no entries for the coming week, returns an empty
list and the agent falls back to generic regional CTAs. The "never invent
locations" rule still applies.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_THIS = Path(__file__).resolve()


def _repo_root() -> Path:
    gha = os.environ.get("GITHUB_WORKSPACE")
    if gha and (Path(gha) / ".git").exists():
        return Path(gha)
    here = _THIS.parent
    for ancestor in [here, *here.parents]:
        if (ancestor / ".git").exists() and (ancestor / "lib").exists():
            return ancestor
    return _THIS.parent.parent


SCHEDULE_PATH = _repo_root() / "state" / "drew-schedule.md"

# Match lines like "## 2026-05-20  Granbury, TX  (multiple barrels)"
_ENTRY_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2})\s+(.+?)\s*$",
    re.MULTILINE,
)


@dataclass
class ScheduleEntry:
    date: datetime          # midnight on the day
    location: str           # raw location text, e.g. "Granbury, TX (multiple barrels)"

    @property
    def day_label(self) -> str:
        return self.date.strftime("%a %b %-d")


def parse_schedule(path: Path = SCHEDULE_PATH) -> list[ScheduleEntry]:
    """Parse the schedule file. Returns [] if missing or empty."""
    if not path.exists():
        return []
    raw = path.read_text()
    out: list[ScheduleEntry] = []
    for m in _ENTRY_RE.finditer(raw):
        date_s, loc = m.group(1), m.group(2).strip()
        # Skip lines marked as placeholder/template (e.g. "<add city>")
        if loc.startswith("<") and loc.endswith(">"):
            continue
        try:
            d = datetime.strptime(date_s, "%Y-%m-%d")
        except ValueError:
            continue
        out.append(ScheduleEntry(date=d, location=loc))
    return out


def upcoming_week(
    entries: list[ScheduleEntry] | None = None,
    *,
    from_date: datetime | None = None,
) -> list[ScheduleEntry]:
    """Return entries falling in the next 7 days from `from_date` (default: today)."""
    if entries is None:
        entries = parse_schedule()
    start = (from_date or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return [e for e in entries if start <= e.date < end]


def format_schedule_block(entries: list[ScheduleEntry]) -> str:
    """Render the prompt block. Empty string when no upcoming entries.

    The block is appended to the Sunday drafting prompt so the model knows
    which cities Drew is actually confirmed at — and is therefore allowed to
    name in CTAs.
    """
    if not entries:
        return (
            "\n## DREW'S CONFIRMED SCHEDULE\n"
            "(none provided for the coming week — write generic regional CTAs "
            "like 'DFW area' / 'North Texas' / 'Great Falls area')\n"
        )
    lines = [
        "",
        "## DREW'S CONFIRMED SCHEDULE — names you ARE allowed to use in CTAs",
        "(if you reference a specific city/event, it MUST appear in this list)",
        "",
    ]
    for e in entries:
        lines.append(f"- {e.day_label}: {e.location}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    entries = upcoming_week()
    print(f"Loaded {len(entries)} upcoming entries from {SCHEDULE_PATH}")
    for e in entries:
        print(f"  {e.day_label}  →  {e.location}")
    print()
    print(format_schedule_block(entries))
