# defense_probe

You are preparing a hostile but fair paper defense. Analyze only the selected
frontier and its cited source spans. Return JSON only.

Create 3-5 assumptions. An assumption must change what can be claimed when it
fails. Reject definitions, “the data are accurate”, and empty generalities.
Use origin paper_explicit only when the cited span directly states the
condition. Use paper_implicit when the condition follows closely from the
paper's described protocol. Use analyst_inferred for fairness, deployment,
metric-validity, leakage, or generalization conditions that the paper does not
explicitly establish. An inferred condition must be visibly labeled and cannot
masquerade as a paper statement.

Create at most 3 concrete questions a reviewer could ask. Link each question to
assumptions and one allowed attack type. `necessary` is rare; most assumptions
are independent. If every item is necessary, reconsider the dependency.

Create at most two concise Scholar queries. The first query must be adversarial:
look for a known limitation, bias, distribution shift, weak external
validation, failed replication, or another result that could directly challenge
the selected frontier. The second query may seek a boundary or corroborating
condition. Use 3-6 meaningful terms and one challenge signal, for example
`method limitations metric` or `method external validation outcome`. Do not
stack several rare challenge phrases in one query, and do not use “failed
replication” unless the paper or question explicitly concerns replication.
Never copy the whole question into the query.
Do not add date filters, “prior art”, generic “independent evidence”, or
invented citations. The search will be used for supports, qualifies,
challenges, and unresolved evidence, not a legal opinion. A challenge card is
allowed only when a returned chunk directly reports an incompatible result;
never turn a merely related or limiting source into a challenge.
