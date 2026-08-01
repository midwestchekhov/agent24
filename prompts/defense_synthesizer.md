# defense_synthesizer

Produce a concise Korean defense report from the selected claim, source spans,
assumptions, attack questions, and chunk-grounded evidence. Return JSON only.

Write the weakest point as a reviewer-facing issue. Keep up to three questions.
Place evidence in supports, qualifies, challenges, or unresolved. A source
without a cited chunk is unresolved. A search miss is not evidence.

The defensible scope must be narrower than or equal to the paper claim. State
conditions, source refs, evidence ids, and excluded scope. Conditional analyst
inference is allowed only with basis_kind=analyst_inference and explicit
conditions. Do not say the paper is valid, novel, or universally superior.

For each assumption return exactly one single-off impact. independent means
the thesis narrows; necessary means the selected claim becomes unsupported.
Do not calculate combinations of assumptions.
