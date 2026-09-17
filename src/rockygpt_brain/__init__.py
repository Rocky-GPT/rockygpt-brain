"""RockyGPT: a grounded, conversational student assistant for Ramapo College."""

import sys

from rockygpt_brain.campus import calculations as _calculations
from rockygpt_brain.campus import formats as _formats
from rockygpt_brain.campus import progress as _progress
from rockygpt_brain.campus import schedules as _schedules

from rockygpt_brain.contracts import Answer, AnswerPart, ChatMessage, ChatRequest

from rockygpt_brain.core import engine as _engine
from rockygpt_brain.core import provider as _provider
from rockygpt_brain.core import render as _render
from rockygpt_brain.core import reviewer as _reviewer
from rockygpt_brain.core import tools as _tools
from rockygpt_brain.core.engine import InvalidAnswer, run_turn

from rockygpt_brain.governance import accounting as _accounting
from rockygpt_brain.governance import budget as _budget
from rockygpt_brain.governance import evidence as _evidence
from rockygpt_brain.governance import limits as _limits
from rockygpt_brain.governance import reconcile as _reconcile

from rockygpt_brain.retrieval import data as _data
from rockygpt_brain.retrieval import exact as _exact
from rockygpt_brain.retrieval import helpers as _helpers
from rockygpt_brain.retrieval import models as _models
from rockygpt_brain.retrieval import processing as _processing
from rockygpt_brain.retrieval.data import CampusData

# Expose compatibility module aliases so existing tests, scripts, and callers
# referencing flat module paths continue to work without modification:
_this = sys.modules[__name__]
_compat_modules = {
    "data": _data,
    "exact": _exact,
    "retrieval_models": _models,
    "retrieval_helpers": _helpers,
    "retrieval_processing": _processing,
    "engine": _engine,
    "provider": _provider,
    "render": _render,
    "reviewer": _reviewer,
    "tools": _tools,
    "accounting": _accounting,
    "budget": _budget,
    "evidence": _evidence,
    "limits": _limits,
    "reconcile": _reconcile,
    "calculations": _calculations,
    "formats": _formats,
    "progress": _progress,
    "schedules": _schedules,
}

for _mod_name, _mod_obj in _compat_modules.items():
    sys.modules[f"rockygpt_brain.{_mod_name}"] = _mod_obj
    setattr(_this, _mod_name, _mod_obj)

__version__ = "1.0.0"

__all__ = [
    "Answer",
    "AnswerPart",
    "CampusData",
    "ChatMessage",
    "ChatRequest",
    "InvalidAnswer",
    "run_turn",
    "__version__",
]
