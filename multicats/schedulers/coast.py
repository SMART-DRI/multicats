from dataclasses import dataclass, field
from datetime import datetime, timedelta

from typing_extensions import override

from ..interfaces import Scheduler
from ..models import JobParams


@dataclass
class CoastConfig:
    carbon_weight: float = 1.0
    delay_weight: float = 1.0
    congestion_weight: float = 1.0
    slot_duration: timedelta = field(default_factory=lambda: timedelta(minutes=15))


def _congestion_potential(load: float) -> float:
    return load * load


def _congestion(
    job_resource: float, background_load: float, other_resource: float
) -> float:
    base = background_load + other_resource
    return _congestion_potential(base + job_resource) - _congestion_potential(base)


def _job_cost(
    carbon_intensity: float,
    energy: float,
    delay: float,
    job_resource: float,
    background_load: float,
    other_resource: float,
    cfg: CoastConfig,
) -> float:
    """Total cost for a single job assignment. delay is in seconds."""
    carbon_cost = carbon_intensity * energy
    cong = _congestion(job_resource, background_load, other_resource)
    return (
        cfg.carbon_weight * carbon_cost
        + cfg.delay_weight * delay
        + cfg.congestion_weight * cong
    )


class CoastScheduler(Scheduler):
    def __init__(self, config: CoastConfig | None = None) -> None:
        self.config: CoastConfig = config or CoastConfig()

    @override
    def assign(
        self,
        jobs: list[JobParams],
        background_load: float,
        carbon_forecast: dict[datetime, float],
    ) -> dict[str, datetime]:
        raise NotImplementedError
