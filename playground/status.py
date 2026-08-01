"""Claim status from assumption toggle state.

This is the reference implementation of invariant 6: the frontend reimplements
exactly this and calls no model to do it. Keep it pure, keep it small, and keep
it something you could port to twenty lines of JavaScript without thinking --
the moment it needs a condition language, the rule table has grown a feature it
was designed not to have.

The whole combination story is `weakest`: rules fire independently, one per
assumption, and the weakest status any of them produces is the answer.
"""

from __future__ import annotations

from .state import ClaimStatus, InteractionSpec, StatusRule

#: strongest to weakest. Index order is the comparison.
ORDER: tuple[ClaimStatus, ...] = ("strong", "conditional", "weak")


def weakest(statuses) -> ClaimStatus:
    """The whole rule-combination policy. No precedence table, no priorities."""
    return max(statuses, key=ORDER.index, default="strong")


def evaluate(
    spec: InteractionSpec, off_ids
) -> tuple[ClaimStatus, list[StatusRule]]:
    """Status for the given set of switched-off assumptions.

    Returns the fired rules alongside it because the interface has to answer
    "why is it conditional?" with the attribution, not just show a badge.
    """
    off = set(off_ids)
    fired = [r for r in spec.status_rules if r.assumption_id in off]
    return weakest([spec.base_status, *(r.status for r in fired)]), fired
