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
ExplainerProvenance = Literal[
    "source_stated", "measured", "derived", "illustrative", "analogical"
]
Precision = Literal["exact", "approximate", "qualitative"]
#: strong -> conditional -> weak. There is no `broken`: switching an assumption
#: off exposes what the claim rests on, it does not rule the authors wrong.
ClaimStatus = Literal["strong", "conditional", "weak"]
EvidenceFacet = Literal["support", "contradict", "boundary", "methodology"]
EvidenceRelation = Literal["supports", "contradicts", "qualifies", "unresolved"]
EvidenceLedgerStatus = Literal["pending", "sufficient", "partial", "failed"]
ClaimRole = Literal["premise", "subclaim", "result", "boundary", "methodology"]
SpanOrigin = Literal["paper", "manual"]
SectionName = Literal[
    "abstract", "intro", "methods", "results", "discussion",
    "references", "acknowledgments", "other",
]


@dataclass
class Span:
    """A pointer back into the source document. Every claim, number and control
    must terminate in one of these or it cannot be rendered."""

    id: str
    page: int
    kind: Literal["paragraph", "caption", "table_cell", "equation", "figure"]
    text: str = ""
    origin: SpanOrigin = "paper"
    section: SectionName = "other"


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
    support_type: Literal["independent", "necessary"] = "independent"


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
    support_type: Literal["independent", "necessary"] = "independent"

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
class EvidenceObligation:
    """A factual question the explainer must resolve before composition.

    Obligations are produced by the large-context analyst, then normalized by
    the evidence controller.  They are intentionally separate from search
    queries: one obligation may need several progressively refined searches.
    """

    id: str
    question: str
    claim_ids: list[str] = field(default_factory=list)
    kind: Literal["support", "contradict", "boundary", "methodology"] = "support"
    required: bool = True


@dataclass
class SearchAction:
    """One OpenAI-selected call to Liner Search Agent."""

    id: str
    obligation_ids: list[str]
    query: str
    rationale: str = ""
    mode: Literal["scholar"] = "scholar"


@dataclass
class EvidenceChunk:
    """A verbatim reference chunk returned by Liner Search Agent."""

    id: str
    content: str
    source_url: str
    source_title: str = ""
    num: int | None = None


@dataclass
class EvidenceRecord:
    """OpenAI's interpretation of one Liner source, grounded in its chunks."""

    id: str
    obligation_ids: list[str]
    query: str
    title: str
    url: str
    snippet: str = ""
    chunks: list[EvidenceChunk] = field(default_factory=list)
    relation: EvidenceRelation = "unresolved"
    confidence: float = 0.0
    rationale: str = ""
    round_index: int = 0


@dataclass
class EvidenceRound:
    index: int
    actions: list[SearchAction] = field(default_factory=list)
    record_ids: list[str] = field(default_factory=list)
    new_sources: int = 0
    new_chunks: int = 0
    sufficient: bool = False
    missing_obligation_ids: list[str] = field(default_factory=list)
    decision: str = ""


@dataclass
class EvidenceLedger:
    """Auditable state of the bounded OpenAI -> Liner -> OpenAI loop."""

    obligations: list[EvidenceObligation] = field(default_factory=list)
    records: list[EvidenceRecord] = field(default_factory=list)
    rounds: list[EvidenceRound] = field(default_factory=list)
    status: EvidenceLedgerStatus = "pending"
    stop_reason: str = ""


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
    support_type: Literal["independent", "necessary"] = "independent"


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
class EvidenceAtom:
    """A small, typed relation the explainer may safely turn into a panel."""

    kind: Literal[
        "variable", "comparison", "formula", "component_delta",
        "sequence", "caption_direction", "terminology",
    ]
    label: str
    value: Any = None
    source_refs: list[str] = field(default_factory=list)
    provenance: ExplainerProvenance = "source_stated"
    precision: Precision = "qualitative"
    extrapolated: bool = False
    notice: str | None = None


@dataclass
class BottleneckSpec:
    question: str
    why_hard: str
    source_claim_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    mechanism_kind: str = "unknown"
    candidate_controls: list[str] = field(default_factory=list)
    candidate_observables: list[str] = field(default_factory=list)
    learning_payoff: float = 0.0
    data_sufficiency: Literal["sufficient", "partial", "insufficient"] = "partial"
    fidelity: Literal["high", "medium", "low"] = "medium"


@dataclass
class PanelSpec:
    primitive: str
    question: str
    model: dict[str, Any] = field(default_factory=dict)
    controls: list[dict[str, Any]] = field(default_factory=list)
    observables: list[dict[str, Any]] = field(default_factory=list)
    feedback: dict[str, str] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    notice: str | None = None


@dataclass
class ExplainerSpec:
    title: str
    thesis: str
    bottleneck: BottleneckSpec
    panels: list[PanelSpec] = field(default_factory=list)
    comparison: dict[str, Any] = field(default_factory=dict)
    glossary: list[dict[str, str]] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    critical_note: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    editorial: dict[str, Any] = field(default_factory=dict)


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
    #: Exactly one of claim_text or source_path is enough to start a run. When
    #: both are supplied, PDF parsing remains available as optional context
    #: while the explicit claim stays the root seed.
    source_path: str | None = None
    claim_text: str | None = None
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
    explainer: ExplainerSpec | None = None
    bottleneck: BottleneckSpec | None = None
    explainer_route: str | None = None
    ablation_components: list[dict[str, Any]] = field(default_factory=list)
    verdict: CriticVerdict | None = None
    artifact: dict | None = None
    profile: UserProfile = field(default_factory=UserProfile)
    selected_claim_id: str | None = None
    root_claim_id: str | None = None
    frontier_claim_id: str | None = None
    critical_path_ids: list[str] = field(default_factory=list)
    path_unsafe: bool = False
    mode: Mode = "quantitative"
    # Additive input fields are kept at the end so existing positional
    # construction of PaperState remains source-compatible.
    source_text: str | None = None
    source_title: str | None = None
    #: One large-context semantic analysis shared by claim selection and
    #: explainer composition. It is internal analysis data, never the UI
    #: artifact itself.
    context_analysis: dict[str, Any] | None = None
    #: External facts are first-class input to composition. ``external`` above
    #: remains a V1.1 compatibility projection; this ledger is authoritative.
    evidence_ledger: EvidenceLedger = field(default_factory=EvidenceLedger)
    #: Optional provider-rendered visualization. Kept outside ``explainer``
    #: panels so an external HTML artifact cannot become source provenance.
    visualization: dict[str, Any] | None = None

    def range_of(self, span_id: str | None) -> tuple[float, float] | None:
        if not span_id:
            return None
        vals = [n.value for n in self.number_pool.values() if n.span_id == span_id]
        return (min(vals), max(vals)) if vals else None
