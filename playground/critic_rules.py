"""Deterministic checks. These run before any LLM critique and they are the
part of the system that actually prevents fabricated numbers.

Keep every rule here pure and fast. If a rule needs judgement, it belongs in
the LLM critic, not here.
"""

from __future__ import annotations

from typing import Iterator

from .state import InteractionSpec, PaperState, Violation


def precheck(spec: InteractionSpec, state: PaperState) -> Iterator[Violation]:
    span_ids = set(state.doc.spans)
    assumption_ids = {
        a.id for a in state.assumptions if a.claim_id == spec.claim_id
    }
    evidence_ids = {
        e.id for e in state.external.get(spec.claim_id, []) if e.id
    }

    claim = next((c for c in state.claims if c.id == spec.claim_id), None)
    if claim is not None:
        for span_id in claim.evidence_span_ids:
            if span_id not in span_ids:
                yield Violation(
                    "UNKNOWN_SPAN_ID",
                    f"claim '{claim.id}' references missing span '{span_id}'",
                )

    for assumption in state.assumptions:
        if assumption.claim_id != spec.claim_id:
            continue
        for err in assumption.validate():
            yield Violation("MALFORMED_ASSUMPTION", err)
        if assumption.span_id and assumption.span_id not in span_ids:
            yield Violation(
                "UNKNOWN_SPAN_ID",
                f"assumption '{assumption.id}' references missing span "
                f"'{assumption.span_id}'",
            )

    for n in spec.iter_numbers():
        if n.provenance == "measured" and n.source_id not in state.number_pool:
            yield Violation("UNGROUNDED_NUMBER",
                            f"{n.value} claims to be measured but has no source")
        if n.provenance == "derived" and not n.formula_refs:
            yield Violation("UNTRACEABLE_DERIVATION",
                            f"{n.value} derived without formula reference")
        for span_id in n.formula_refs:
            if span_id not in span_ids:
                yield Violation(
                    "UNKNOWN_SPAN_ID",
                    f"derived number {n.value} references missing formula "
                    f"span '{span_id}'",
                )

    for c in spec.controls:
        for err in c.validate():
            yield Violation("MALFORMED_CONTROL", err)
        if c.span_id and c.span_id not in span_ids:
            yield Violation(
                "UNKNOWN_SPAN_ID",
                f"control '{c.name}' references missing span '{c.span_id}'",
            )
        rng = state.range_of(c.span_id)
        if rng and c.min is not None and c.max is not None:
            if c.min < rng[0] or c.max > rng[1]:
                yield Violation(
                    "EXTRAPOLATION_UNMARKED",
                    f"control '{c.name}' spans {c.min}-{c.max}, paper covers "
                    f"{rng[0]}-{rng[1]}",
                    fatal=False,  # allowed if the UI shows a warning band
                )

    for rule in spec.status_rules:
        if rule.assumption_id not in assumption_ids:
            yield Violation(
                "UNKNOWN_ASSUMPTION_ID",
                f"status rule references missing assumption "
                f"'{rule.assumption_id}'",
            )

        attribution = rule.attribution
        for err in attribution.validate():
            yield Violation("MALFORMED_ATTRIBUTION", err)
        if (attribution.kind == "paper" and attribution.span_id
                and attribution.span_id not in span_ids):
            yield Violation(
                "UNKNOWN_SPAN_ID",
                f"status rule for '{rule.assumption_id}' references missing "
                f"span '{attribution.span_id}'",
            )
        if (attribution.kind == "paper" and attribution.span_id
                and attribution.span_id in state.doc.spans
                and state.doc.spans[attribution.span_id].origin != "paper"):
            yield Violation(
                "NON_PAPER_ATTRIBUTION",
                f"status rule for '{rule.assumption_id}' labels manual input "
                f"span '{attribution.span_id}' as paper",
            )
        if (attribution.kind == "external" and attribution.evidence_id
                and attribution.evidence_id not in evidence_ids):
            yield Violation(
                "UNKNOWN_EVIDENCE_ID",
                f"status rule for '{rule.assumption_id}' references missing "
                f"evidence '{attribution.evidence_id}'",
            )

    if spec.numbers and not any(
        n.provenance == "measured" for n in spec.numbers
    ) and not spec.fidelity_warning:
        yield Violation("ILLUSTRATIVE_WITHOUT_WARNING",
                        "no measured numbers and no fidelity warning")
