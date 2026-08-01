"""Single mutable state object passed through every stage.

Design rule: nothing in the pipeline may hold private state. If a stage needs
to remember something, it goes here. This is what makes incremental recompute
possible -- invalidating a field is enough to know which stages must rerun.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["quantitative", "qualitative", "refused"]
Provenance = Literal["variable", "assumption", "pedagogical_simplification"]


@dataclass
class Span:
    """A pointer back into the source document. Every claim, number and control
    must terminate in one of these or it cannot be rendered."""

    id: str
    page: int
    kind: Literal["paragraph", "caption", "table_cell", "equation", "figure"]
    text: str = ""


@dataclass
class NumberFact:
    """Every numeric literal found in the paper, indexed at parse time.
    The Critic checks generated numbers against this pool -- that check is
    deterministic, not an LLM call."""

    id: str
    value: float
    raw: str
    span_id: str
    unit: str | None = None
    context: str = ""


@dataclass
class Claim:
    id: str
    text: str
    evidence_span_ids: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    figure_id: str | None = None
    confidence: float = 0.5
    novelty_marker: bool = False


@dataclass
class InteractionScore:
    claim_id: str
    manipulability: float
    causal_clarity: float
    learning_value: float
    faithfulness: float
    demo_reliability: float

    @property
    def total(self) -> float:
        # faithfulness is weighted hardest: a beautiful but ungrounded
        # interaction is worse than no interaction.
        return (
            self.manipulability * 0.2
            + self.causal_clarity * 0.2
            + self.learning_value * 0.2
            + self.faithfulness * 0.3
            + self.demo_reliability * 0.1
        )


@dataclass
class Evidence:
    """External evidence retrieved from search. Never merged into paper-derived
    facts -- kept in a separate field so the UI can label it."""

    claim_id: str
    title: str
    url: str
    snippet: str
    stance: Literal["supports", "contradicts", "unclear"] = "unclear"


@dataclass
class Control:
    name: str
    kind: Literal["slider", "toggle", "select"]
    provenance: Provenance
    span_id: str | None = None  # required when provenance == "variable"
    min: float | None = None
    max: float | None = None
    default: Any = None

    def validate(self) -> list[str]:
        errs = []
        if self.provenance == "variable" and not self.span_id:
            errs.append(f"control '{self.name}': variable without span_id")
        if self.kind == "slider" and (self.min is None or self.max is None):
            errs.append(f"control '{self.name}': slider without range")
        return errs


@dataclass
class SpecNumber:
    """A number the artifact will display. provenance decides how hard the
    Critic checks it."""

    value: float
    provenance: Literal["measured", "derived", "illustrative"]
    source_id: str | None = None       # NumberFact.id when measured
    formula_refs: list[str] = field(default_factory=list)  # span ids when derived


@dataclass
class InteractionSpec:
    claim_id: str
    primitive: str
    title: str
    learning_goal: str
    misconception: str
    controls: list[Control] = field(default_factory=list)
    numbers: list[SpecNumber] = field(default_factory=list)
    series: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, str] = field(default_factory=dict)  # level -> text
    fidelity_warning: str | None = None

    def iter_numbers(self):
        return iter(self.numbers)


@dataclass
class Violation:
    code: str
    detail: str
    fatal: bool = True


@dataclass
class CriticVerdict:
    result: Literal["PASS", "REVISE", "HUMAN_CONFIRMATION_REQUIRED"]
    violations: list[Violation] = field(default_factory=list)


@dataclass
class UserProfile:
    level: Literal["novice", "domain_student", "expert"] = "domain_student"
    purpose: Literal["exam", "journal_club", "review"] = "journal_club"
    language: str = "ko"
    freeform: str | None = None  # "비유 말고 수식으로" 같은 임의 요청


@dataclass
class DocGraph:
    spans: dict[str, Span] = field(default_factory=dict)
    figures: dict[str, dict] = field(default_factory=dict)
    sections: list[dict] = field(default_factory=list)


@dataclass
class PaperState:
    source_path: str
    doc: DocGraph = field(default_factory=DocGraph)
    number_pool: dict[str, NumberFact] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    scores: dict[str, InteractionScore] = field(default_factory=dict)
    external: dict[str, list[Evidence]] = field(default_factory=dict)
    spec: InteractionSpec | None = None
    verdict: CriticVerdict | None = None
    artifact: dict | None = None
    profile: UserProfile = field(default_factory=UserProfile)
    selected_claim_id: str | None = None
    mode: Mode = "quantitative"
    revise_count: int = 0

    def range_of(self, span_id: str | None) -> tuple[float, float] | None:
        if not span_id:
            return None
        vals = [n.value for n in self.number_pool.values() if n.span_id == span_id]
        return (min(vals), max(vals)) if vals else None
