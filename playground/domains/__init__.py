"""Domain packs.

The med/ML decision lives here and nowhere else. A pack is just a dict of
primitive name -> the fields its renderer needs. Adding a domain must never
require touching pipeline.py or stages/.

The legacy `DesignInteraction` emits `assumption_switchboard`; V2 panel
primitives are composed from the source context by `PanelComposer`. Keeping
both registries makes the fallback boundary explicit and domain-isolated.
"""

#: every pack carries this: the switchboard is domain-independent, which is
#: the point of it. What differs per domain is which other primitives exist.
SWITCHBOARD = {
    "assumption_switchboard": {
        "needs": ["assumptions", "status_rules"],
        "recovers_from": ["paragraph", "table", "caption"],
    },
}

MED = {
    **SWITCHBOARD,
    "threshold_explorer": {
        "needs": ["sensitivity", "specificity", "threshold_range"],
        "recovers_from": ["table", "caption"],
    },
    "survival_curve_explorer": {
        "needs": ["hazard_ratio", "time_points", "n_at_risk"],
        "recovers_from": ["table", "text"],
    },
    "forest_plot_explorer": {
        "needs": ["effect_sizes", "confidence_intervals", "study_labels"],
        "recovers_from": ["table"],
    },
    "annotated_figure": {"needs": [], "recovers_from": ["figure"]},
}

ML = {
    **SWITCHBOARD,
    "generated_schematic": {
        "needs": ["caption_or_prose_relation"],
        "recovers_from": ["paragraph", "caption", "equation"],
    },
    "scaling_comparison": {
        "needs": ["x_values", "baseline_series", "proposed_series"],
        "recovers_from": ["equation", "table"],
    },
    "ablation_toggle": {
        "needs": ["components", "deltas"],
        "recovers_from": ["table"],
    },
    "annotated_figure": {"needs": [], "recovers_from": ["figure"]},
}

PACKS = {"med": MED, "ml": ML}


def get_pack(name: str) -> dict:
    if name not in PACKS:
        raise KeyError(f"unknown domain '{name}', have {list(PACKS)}")
    return PACKS[name]
