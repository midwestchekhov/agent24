"""Deterministic checks. These run before any LLM critique and they are the
part of the system that actually prevents fabricated numbers.

Keep every rule here pure and fast. If a rule needs judgement, it belongs in
the LLM critic, not here.
"""

from __future__ import annotations

from typing import Iterator

from .state import InteractionSpec, PaperState, Violation


def precheck(spec: InteractionSpec, state: PaperState) -> Iterator[Violation]:
    for n in spec.iter_numbers():
        if n.provenance == "measured" and n.source_id not in state.number_pool:
            yield Violation("UNGROUNDED_NUMBER",
                            f"{n.value} claims to be measured but has no source")
        if n.provenance == "derived" and not n.formula_refs:
            yield Violation("UNTRACEABLE_DERIVATION",
                            f"{n.value} derived without formula reference")

    for c in spec.controls:
        for err in c.validate():
            yield Violation("MALFORMED_CONTROL", err)
        rng = state.range_of(c.span_id)
        if rng and c.min is not None and c.max is not None:
            if c.min < rng[0] or c.max > rng[1]:
                yield Violation(
                    "EXTRAPOLATION_UNMARKED",
                    f"control '{c.name}' spans {c.min}-{c.max}, paper covers "
                    f"{rng[0]}-{rng[1]}",
                    fatal=False,  # allowed if the UI shows a warning band
                )

    if spec.numbers and not any(
        n.provenance == "measured" for n in spec.numbers
    ) and not spec.fidelity_warning:
        yield Violation("ILLUSTRATIVE_WITHOUT_WARNING",
                        "no measured numbers and no fidelity warning")
