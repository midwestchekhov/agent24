"""Stage registry.

Every stage is defined in its own module; this package is the single import
surface for the pipeline. Adding a stage means adding a module here and one
line to `Pipeline.build` -- nothing else imports the stage modules directly.
"""

from .assumptions import AssumptionMiner
from .base import Stage, StageError
from .claims import BuildClaims, ScoreInteractions, SelectClaim, SelectFrontier
from .context import ContextAnalyst
from .critic import Critic, VisualizationAdapter
from .explainer import BottleneckMiner, KoreanEditorial, PanelComposer
from .external import VerifyExternal
from .parse import Parse
from .render import Render
from .switchboard import build_panel as build_switchboard_panel

__all__ = [
    "AssumptionMiner",
    "build_switchboard_panel",
    "BottleneckMiner",
    "BuildClaims",
    "ContextAnalyst",
    "Critic",
    "KoreanEditorial",
    "PanelComposer",
    "Parse",
    "Render",
    "ScoreInteractions",
    "SelectClaim",
    "SelectFrontier",
    "Stage",
    "StageError",
    "VerifyExternal",
    "VisualizationAdapter",
]
