# Context analyst

Read the supplied source spans as one context. Return JSON matching
`ContextAnalysis` with:

- `claims`: 1-8 source-bound claims with `evidence_span_ids`, `parent_id`,
  `role`, `support_type`, and `order`;
- `relations`: only relationships supported by cited spans;
- `mechanisms`: candidate explanatory mechanisms with entities, relations,
  observables, and evidence references;
- `bottleneck`: exactly one best teaching question, with evidence references;
- `assumptions`: conditions that are explicitly or implicitly supported;
- `quantitative_facts`: number-pool ids only;
- `search_obligations`: 1-6 factual questions the external evidence loop must
  resolve, each with `id`, `question`, `claim_ids`, `kind` (`support`,
  `contradict`, `boundary`, or `methodology`), and `required`;
- `limitations`: missing or non-recoverable evidence.

Never use bibliography, acknowledgments, author metadata, or figure pixels as
claim evidence. Do not turn the claim graph into a UI design. All numbers and
relations must carry source span references and must be labelled as measured,
derived, or illustrative.

A claim must be a falsifiable assertion, not a heading, topic label, table
description, metric definition, dataset/configuration list, or the fact that a
table contains values. The root is the paper's thesis. For Guo-style
calibration papers, prefer the asserted relationship (accuracy can improve
while calibration worsens; a method corrects it) over a table of ECE values.

Search obligations are not search-engine strings. State what fact would need
to be established or challenged. Cover at least direct independent evidence
and an important boundary or failure condition. Do not guess paper titles,
authors, venues, URLs, or identifiers.
