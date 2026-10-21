"""Scheduling algorithms.

``SCHEDULERS`` lists the schedulers that ``multicats schedule --scheduler`` can
pick. To add one, implement :class:`multicats.interfaces.Scheduler`, including
``description``, ``add_arguments()`` and ``from_args()``, and register it here.
"""

from ..interfaces import Scheduler
from .coast import CoastScheduler
from .random import RandomScheduler

SCHEDULERS: dict[str, type[Scheduler]] = {
    "coast": CoastScheduler,
    "random": RandomScheduler,
}
DEFAULT_SCHEDULER = "coast"

__all__ = ["DEFAULT_SCHEDULER", "SCHEDULERS", "CoastScheduler", "RandomScheduler"]
