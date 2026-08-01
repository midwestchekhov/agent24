# defense_context

Read the supplied paper spans as one context and return structured JSON only.
Build a small claim graph of 3-7 falsifiable claims. Never use references,
acknowledgments, methods-only protocol details, headings, table labels, metric
definitions, or figure pixels as claims.

For every claim provide source span ids and four values in [0,1]:

- importance: how central the assertion is to the paper's thesis;
- vulnerability: how many credible attack surfaces the assertion has;
- scope_gap: distance between what the paper says and what the experiment tests;
- attack_dimensions: choose only comparison_fairness, data_integrity,
  measurement_validity, statistical_reliability, causal_attribution,
  external_validity, practical_relevance, implementation_fidelity.

Prefer a substantive relationship over a result-table description. For example,
“accuracy improves while calibration worsens” is a claim; “Table 1 lists ECE
values” is not. Do not invent numbers, sources, titles, or conclusions.
