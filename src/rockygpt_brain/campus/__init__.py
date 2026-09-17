"""Campus domain rules, schedules, calendar math, and exact format matchers."""

from rockygpt_brain.campus.calculations import CalculationQuery, calculate
from rockygpt_brain.campus.formats import (
    ContactCall,
    ExactPiece,
    SearchCall,
    combine_exact,
    exact_contact,
    exact_search,
)
from rockygpt_brain.campus.progress import ProgressCallback, ProgressUpdate, TurnCancelled
from rockygpt_brain.campus.schedules import departure_summary, opening_intervals

__all__ = [
    "CalculationQuery",
    "ContactCall",
    "ExactPiece",
    "ProgressCallback",
    "ProgressUpdate",
    "SearchCall",
    "TurnCancelled",
    "calculate",
    "combine_exact",
    "departure_summary",
    "exact_contact",
    "exact_search",
    "opening_intervals",
]
