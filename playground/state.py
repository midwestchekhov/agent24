"""Single mutable state object passed through every stage.

Design rule: nothing in the pipeline may hold private state. If a stage needs
to remember something, it goes here. One state object makes the autonomous run
and its final payload auditable from start to finish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["quantitative", "qualitative", "refused"]
Provenance = Literal["variable", "assumption", "pedagogical_simplification"]
#: strong -> conditional -> weak. There is no `broken`: switching an assumption
#: off exposes what the claim rests on, it does not rule the authors wrong.
ClaimStatus = Literal["strong", "conditional", "weak"]
EvidenceFacet = Literal["support", "contradict", "boundary", "methodology"]
ClaimRole = Literal["premise", "subclaim", "result", "boundary", "methodology"]


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
    parent_id: str | None = None
    role: ClaimRole = "subclaim"
    order: int = 0
    difficulty: float = 0.5
    pedagogical_gain: float = 0.5


@dataclass
class Assumption:
    """A condition the selected claim rests on -- the thing the user gets to
    switch off. `weakens_how` is what separates an assumption from background:
    if you cannot say what the claim loses when this fails, toggling it teaches
    nothing and it does not belong here."""

    id: str
    claim_id: str
    text: str
    kind: Literal["scope", "measurement", "generalization", "implementation"]
    source: Literal["paper_explicit", "paper_implicit", "pedagogical"]
    weakens_how: str
    span_id: str | None = None  # required unless source == "pedagogical"

    def validate(self) -> list[str]:
        errs = []
        if self.source != "pedagogical" and not self.span_id:
            errs.append(f"assumption '{self.id}': {self.source} without span_id")
        if not self.weakens_how.strip():
            errs.append(f"assumption '{self.id}': no weakens_how")
        return errs


@dataclass
class InteractionScore:
    claim_id: str
    manipulability: float
    causal_clarity: float
    learning_value: float
    faithfulness: float
    demo_reliability: float
    difficulty: float = 0.5
    pedagogical_gain: float = 0.5

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

    @property
    def frontier_total(self) -> float:
        """Score for choosing the node with the most learning leverage."""
        return (
            self.faithfulness * 0.25
            + self.pedagogical_gain * 0.20
            + self.difficulty * 0.15
            + self.manipulability * 0.15
            + self.causal_clarity * 0.10
            + self.learning_value * 0.10
            + self.demo_reliability * 0.05
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
    #: what an Attribution of kind "external" points at. Kept in its original
    #: positional slot for callers constructed before facets were added.
    id: str = ""
    #: Search lens(es) that surfaced this source. A facet records how we looked
    #: for the source, not what the source proves; it is never a controversy
    #: judgement. One URL may be found through more than one lens.
    facets: list[EvidenceFacet] = field(default_factory=list)
    #: A path-level search result can be relevant to more than one claim node.
    covered_claim_ids: list[str] = field(default_factory=list)


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
class Attribution:
    """Invariant 7. Why a status moved always points at something that exists.
    `pedagogical` is the honest answer when nothing does -- the interface
    labels it as ours rather than the paper's."""

    kind: Literal["paper", "external", "pedagogical"]
    span_id: str | None = None       # required when kind == "paper"
    evidence_id: str | None = None   # required when kind == "external"

    def validate(self) -> list[str]:
        errs = []
        if self.kind == "paper" and not self.span_id:
            errs.append("paper attribution without span_id")
        if self.kind == "external" and not self.evidence_id:
            errs.append("external attribution without evidence_id")
        return errs


@dataclass
class StatusRule:
    """Where the claim's status goes when one assumption is switched off.

    There is no `when` field on purpose. A rule always means "when this
    assumption is off" -- open that up into a condition and combination rules
    move in, which is the thing the frontend evaluator must never have to
    understand. One rule, one assumption.
    """

    assumption_id: str
    status: Literal["conditional", "weak"]  # strong is the base, not a rule
    because: str                            # one sentence, shown to the reader
    attribution: Attribution


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
    #: status with every assumption still on. Usually strong, but a thinly
    #: evidenced claim can start out conditional.
    base_status: ClaimStatus = "strong"
    #: generated once here, at design time, and evaluated in the frontend --
    #: invariant 6. A toggle must never cost an LLM call.
    status_rules: list[StatusRule] = field(default_factory=list)

    def iter_numbers(self):
        return iter(self.numbers)


@dataclass
class Violation:
    code: str
    detail: str
    fatal: bool = True


@dataclass
class CriticVerdict:
    result: Literal["PASS", "UNSAFE_TO_VISUALIZE"]
    violations: list[Violation] = field(default_factory=list)


@dataclass
class ClaimAnalysis:
    """Detailed analysis for one node on the selected root-to-frontier path."""

    claim_id: str
    verification: Literal["verified", "unverified", "failed"]
    explanation: str = ""
    assumptions: list[Assumption] = field(default_factory=list)
    evidence_span_ids: list[str] = field(default_factory=list)


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
    #: the automatically selected claim's assumptions only -- decomposing every
    #: candidate up front would make cost scale with the number of claims.
    assumptions: list[Assumption] = field(default_factory=list)
    claim_analyses: dict[str, ClaimAnalysis] = field(default_factory=dict)
    scores: dict[str, InteractionScore] = field(default_factory=dict)
    external: dict[str, list[Evidence]] = field(default_factory=dict)
    spec: InteractionSpec | None = None
    verdict: CriticVerdict | None = None
    artifact: dict | None = None
    profile: UserProfile = field(default_factory=UserProfile)
    selected_claim_id: str | None = None
    root_claim_id: str | None = None
    frontier_claim_id: str | None = None
    critical_path_ids: list[str] = field(default_factory=list)
    path_unsafe: bool = False
    mode: Mode = "quantitative"

    def range_of(self, span_id: str | None) -> tuple[float, float] | None:
        if not span_id:
            return None
        vals = [n.value for n in self.number_pool.values() if n.span_id == span_id]
        return (min(vals), max(vals)) if vals else None
