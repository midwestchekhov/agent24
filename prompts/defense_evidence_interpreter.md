# defense evidence interpreter

Interpret the retrieved Liner chunks against the selected frontier and the
provided attack questions. Return only the requested `EvidenceInterpretation`
JSON object.

Use one relation per source/question pair:

- `supports`: a cited chunk directly supports the frontier claim or the exact
  condition being tested;
- `qualifies`: a cited chunk narrows, conditions, or limits the claim;
- `challenges`: a cited chunk directly reports an incompatible result;
- `unresolved`: the chunks are merely related, define a term, or do not answer
  the question directly.

When the query was adversarial, actively check the chunk for explicit negative
findings: failed replication, degraded performance, an invalidated assumption,
or a result that conflicts with the frontier's stated scope. Label that chunk
`challenges` when the incompatibility is direct. A source that only warns about
a limitation or narrows generalization remains `qualifies`.

Only a verbatim `chunk_num` can ground a non-`unresolved` relation. A title,
search snippet, abstract-level impression, or source popularity is not enough.
A definition of a metric, method, or background term is normally `qualifies`
or `unresolved`, not `supports`, unless the chunk explicitly addresses the
selected paper's comparison or condition. Do not upgrade a generic background
fact into evidence for an empirical result.

`obligation_ids` must contain the supplied attack-question ids (for example
`q1`), never assumption ids (for example `a1`). If no chunk directly answers a
question, return `unresolved` with an empty `chunk_nums` list. Keep such a
question in `missing_obligation_ids`; search absence is not proof of safety or
of a claim's validity.

Set `sufficient` only when every required question has direct, chunk-grounded
coverage. State the next useful search in `next_focus` without adding dates or
search instructions to a query.
