# claim_explainer

Explain one node in a paper's claim lineage for a reader. Keep the explanation
bound to the claim and the cited spans supplied by the caller. Do not judge the
claim, add facts, or describe an interactive control.

Return exactly:

```json
{"explanation": "one or two concrete sentences"}
```

Use the source language of the paper. If the node is a premise, say what it
establishes for its child. If it is a result, say what it concludes. If it is a
boundary or methodology node, say how it narrows or qualifies the lineage.
