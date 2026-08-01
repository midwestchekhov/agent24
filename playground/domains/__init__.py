"""Domain packs.

The med/ML decision lives here and nowhere else. A pack is just a dict of
primitive name -> the fields its renderer needs. Adding a domain must never
require touching pipeline.py or stages/.
"""

MED = {
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
