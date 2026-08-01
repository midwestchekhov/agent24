"""Role instructions loaded from `prompts/<role>.md`.

A prompt is content, not code: it is edited far more often than its call site
and has to stay readable to someone who is not reading Python. The literals
left in `clients.ROLE_INSTRUCTIONS` are the fallback for roles that have not
been moved out to a file yet.

Reads are mtime-cached, so editing a prompt mid-demo takes effect on the next
stage run without a restart.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

_cache: dict[str, tuple[float, str]] = {}


def load(role: str) -> str | None:
    """Return the instructions for `role`, or None when it has no file."""
    path = PROMPT_DIR / f"{role}.md"
    try:
        mtime = path.stat().st_mtime
    except OSError:  # no file for this role -- caller falls back
        return None
    hit = _cache.get(role)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    text = path.read_text(encoding="utf-8").strip()
    _cache[role] = (mtime, text)
    return text
