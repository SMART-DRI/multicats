"""Abstract interfaces for the three pluggable components."""

from abc import ABC, abstractmethod
from datetime import datetime

from .models import JobParams


class QueueProvider(ABC):
    """Reads cluster state: background load and per-job energy estimates."""

    @abstractmethod
    def background_load(self) -> float:
        """Return current background resource load as a fraction of cluster capacity."""

    @abstractmethod
    def pending_jobs(self) -> list[JobParams]:
        """Return the list of jobs currently awaiting scheduling decisions."""

    @abstractmethod
    def energy_estimate(self, job_id: str) -> float:
        """Return the estimated energy consumption (kWh) for a given job."""


class Scheduler(ABC):
    """Assigns a start slot to each pending job.

    Each concrete scheduler carries its own configuration (passed via __init__).
    The generic interface only exposes the assign() method so the daemon loop
    can drive any scheduler uniformly.
    """

    @abstractmethod
    def assign(
        self,
        jobs: list[JobParams],
        background_load: float,
        carbon_forecast: dict[datetime, float],
    ) -> dict[str, datetime]:
        """Return a mapping of job_id -> assigned start time for all jobs."""


class Controller(ABC):
    """Submits or holds jobs in the cluster scheduler."""

    @abstractmethod
    def hold(self, job_id: str) -> None:
        """Prevent a job from starting until explicitly released."""

    @abstractmethod
    def release(self, job_id: str, start: datetime) -> None:
        """Allow a job to start at the given time."""

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        """Cancel a job entirely."""
