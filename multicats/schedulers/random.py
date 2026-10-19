import argparse
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from typing_extensions import Self, override

from ..interfaces import Scheduler
from ..models import JobParams


@dataclass
class RandomConfig:
    window: timedelta = field(default_factory=lambda: timedelta(hours=48))
    seed: int | None = None


class RandomScheduler(Scheduler):
    description = "uniformly random start times (baseline)"

    @override
    @classmethod
    def add_arguments(cls, group: argparse._ArgumentGroup) -> None:  # pyright: ignore[reportPrivateUsage]
        _ = group.add_argument(
            "--window-hours",
            type=float,
            default=RandomConfig().window / timedelta(hours=1),
            help="latest start, in hours after submission (default: %(default)s)",
        )
        _ = group.add_argument("--seed", type=int, help="random seed")

    @override
    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        return cls(
            RandomConfig(window=timedelta(hours=args.window_hours), seed=args.seed)
        )

    def __init__(self, config: RandomConfig | None = None) -> None:
        self.config: RandomConfig = config or RandomConfig()
        self._rng = random.Random(self.config.seed)

    @override
    def assign(
        self,
        jobs: list[JobParams],
        background_load: float,
    ) -> dict[str, datetime]:
        result = {}
        for job in jobs:
            lo = job.earliest_start()
            hi = min(job.latest_start(), job.arrival + self.config.window)
            if hi <= lo:
                result[job.job_id] = lo
            else:
                span = (hi - lo).total_seconds()
                result[job.job_id] = lo + timedelta(seconds=self._rng.uniform(0, span))
        return result
