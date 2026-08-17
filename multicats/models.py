from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class JobParams:
    """Parameters describing a single schedulable job."""

    job_id: str
    arrival: datetime  # a: when the job intent arrives
    resource: float  # r: normalised resource demand (e.g. fraction of cluster)
    duration: timedelta  # d: user-provided runtime
    energy: float  # e_hat: estimated total energy consumption (kWh)
    deadline: datetime  # D: latest time by which the job must complete
    max_wait: timedelta  # m: maximum acceptable waiting time

    def earliest_start(self) -> datetime:
        return self.arrival

    def latest_start(self) -> datetime:
        return min(self.deadline - self.duration, self.arrival + self.max_wait)

    def is_feasible(self, start: datetime) -> bool:
        return self.earliest_start() <= start <= self.latest_start()
