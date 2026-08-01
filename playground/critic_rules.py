"""Deterministic checks. These run before any LLM critique and they are the
part of the system that actually prevents fabricated numbers.

Keep every rule here pure and fast. If a rule needs judgement, it belongs in
the LLM critic, not here.
"""

from __future__ import annotations

import re
from typing import Iterator

from .state import InteractionSpec, PaperState, Violation

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[가-힣]{2,}")
STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "when", "then",
    "claim", "paper", "결과", "주장", "경우", "대한", "있는", "있다",
}


def attribution_support(rule, assumption, state: PaperState) -> tuple[bool, float]:
    """Return numeric support and lexical overlap for a paper attribution."""
    span = state.doc.spans.get(rule.attribution.span_id or "")
    if span is None:
        return False, 0.0
    text = f"{rule.because} {assumption.text} {assumption.weakens_how}"
    mentioned = [float(m.group(0)) for m in re.finditer(
        r"-?\d+(?:\.\d+)?", text
    )]
    facts = [n.value for n in state.number_pool.values()
             if n.span_id == span.id]
    if mentioned:
        numeric_ok = all(any(abs(value - fact) < 1e-6 for fact in facts)
                         for value in mentioned)
    else:
        numeric_ok = True
    source_tokens = {t.lower() for t in TOKEN_RE.findall(span.text)
                     if t.lower() not in STOPWORDS}
    cited_tokens = {t.lower() for t in TOKEN_RE.findall(text)
                    if t.lower() not in STOPWORDS}
    overlap = (len(source_tokens & cited_tokens) / max(len(cited_tokens), 1)
               if cited_tokens else 0.0)
    return numeric_ok, overlap


def precheck(spec: InteractionSpec, state: PaperState) -> Iterator[Violation]:
    span_ids = set(state.doc.spans)
    assumption_ids = {
        a.id for a in state.assumptions if a.claim_id == spec.claim_id
    }
    evidence_ids = {record.id for record in state.evidence_ledger.records}
    evidence_by_id = {record.id: record for record in state.evidence_ledger.records}
    obligation_ids = {item.id for item in state.evidence_ledger.obligations}

    for record in state.evidence_ledger.records:
        if not record.url:
            yield Violation(
                "EVIDENCE_WITHOUT_URL",
                f"evidence '{record.id}' has no source URL",
            )
        unknown = [item for item in record.obligation_ids if item not in obligation_ids]
        if unknown:
            yield Violation(
                "UNKNOWN_EVIDENCE_OBLIGATION",
                f"evidence '{record.id}' references unknown obligations {unknown}",
            )
        if record.relation != "unresolved" and not record.chunks:
            yield Violation(
                "EVIDENCE_RELATION_WITHOUT_CHUNK",
                f"evidence '{record.id}' is {record.relation} without a reference chunk",
            )
        for chunk in record.chunks:
            if chunk.source_url.rstrip("/") != record.url.rstrip("/"):
                yield Violation(
                    "EVIDENCE_CHUNK_SOURCE_MISMATCH",
                    f"chunk '{chunk.id}' does not belong to evidence '{record.id}'",
                )

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
        if (attribution.kind == "paper" and attribution.span_id
                and attribution.span_id in span_ids):
            assumption = next(
                (a for a in state.assumptions if a.id == rule.assumption_id),
                None,
            )
            if assumption is not None:
                numeric_ok, overlap = attribution_support(rule, assumption, state)
                if not numeric_ok:
                    yield Violation(
                        "UNSUPPORTED_NUMERIC_ATTRIBUTION",
                        f"status rule '{rule.assumption_id}' numbers are not present in span "
                        f"'{attribution.span_id}'",
                    )
                elif overlap < 0.10:
                    yield Violation(
                        "WEAK_TEXT_ATTRIBUTION",
                        f"status rule '{rule.assumption_id}' has only {overlap:.2f} token overlap "
                        f"with span '{attribution.span_id}'",
                        fatal=False,
                    )

    if spec.numbers and not any(
        n.provenance == "measured" for n in spec.numbers
    ) and not spec.fidelity_warning:
        yield Violation("ILLUSTRATIVE_WITHOUT_WARNING",
                        "no measured numbers and no fidelity warning")

    explainer = state.explainer
    if explainer is not None:
        if len(explainer.panels) == 0 or len(explainer.panels) > 3:
            yield Violation(
                "PANEL_COUNT_OUT_OF_RANGE",
                f"explainer must contain 1-3 panels, got {len(explainer.panels)}",
            )
        # The vocabulary is verbs, not picture kinds: every primitive here can
        # be filled from the text layer alone. A picture-kind primitive
        # (annotated_figure) would need figure pixels we deliberately never read.
        allowed = {"rate_compare", "threshold_finder", "part_removal",
                   "flow_topology", "proportion_reveal"}
        for panel in explainer.panels:
            model = panel.model if isinstance(panel.model, dict) else {}
            if panel.primitive not in allowed:
                yield Violation("UNKNOWN_PANEL_PRIMITIVE",
                                f"unsupported panel primitive '{panel.primitive}'")
            for datum in panel.provenance:
                if not isinstance(datum, dict):
                    continue
                if datum.get("provenance") in {"illustrative", "analogical"} \
                        and not panel.notice:
                    yield Violation(
                        "ILLUSTRATIVE_WITHOUT_WARNING",
                        f"panel '{panel.primitive}' contains "
                        f"{datum.get('provenance')} data without notice",
                    )
                for span_id in datum.get("source_refs") or []:
                    if span_id not in span_ids:
                        yield Violation(
                            "UNKNOWN_SPAN_ID",
                            f"panel '{panel.primitive}' provenance references "
                            f"missing span '{span_id}'",
                        )
                for evidence_id in datum.get("evidence_refs") or []:
                    if evidence_id not in evidence_ids:
                        yield Violation(
                            "UNKNOWN_EVIDENCE_ID",
                            f"panel '{panel.primitive}' provenance references "
                            f"missing evidence '{evidence_id}'",
                        )
                        continue
                    record = evidence_by_id[evidence_id]
                    if (record.relation == "unresolved" or not record.chunks
                            or record.confidence < 0.5):
                        yield Violation(
                            "UNRESOLVED_EVIDENCE_USED",
                            f"panel '{panel.primitive}' relies on unresolved "
                            f"evidence '{evidence_id}'",
                        )
            if panel.primitive == "part_removal":
                if model.get("metric") == "status":
                    # The rule table is the whole interaction here. Without it
                    # the panel is a row of switches that move nothing.
                    rules = model.get("rules")
                    if not isinstance(rules, list) or not rules:
                        yield Violation(
                            "SWITCHBOARD_WITHOUT_RULES",
                            "part_removal(status) requires at least one status rule",
                        )
                else:
                    parts = model.get("parts")
                    if not isinstance(parts, list) or not parts:
                        yield Violation(
                            "ABLATION_WITHOUT_DELTAS",
                            "part_removal(numeric) requires source-bound part deltas",
                        )
