"""Errors shared by layers that must not import each other.

`clients` sits below `stages`, so it cannot reach into `stages.base` for the
exception type it raises. Both import it from here instead. `stages.base` and
`clients` re-export the name, so existing import paths keep working.
"""

from __future__ import annotations


class StageError(Exception):
    """Raised when a stage cannot produce output. The pipeline decides whether
    to degrade the mode or abort -- the stage does not decide."""
