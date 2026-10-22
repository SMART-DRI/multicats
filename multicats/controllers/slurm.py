import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from typing_extensions import override

from ..interfaces import Controller
from ..slurm import run_slurm


@dataclass
class SlurmControllerConfig:
    min_lead_time: timedelta = timedelta(minutes=1)


class SlurmController(Controller):
    """Controls Slurm jobs via scontrol/scancel CLI commands."""

    def __init__(self, config: SlurmControllerConfig | None = None) -> None:
        self.config: SlurmControllerConfig = config or SlurmControllerConfig()

    def _effective_start(self, start: datetime) -> datetime:
        """Clamp start to at least min_lead_time from now.

        Slurm silently discards past StartTimes, so we enforce a minimum
        lead time before applying any assignment.
        """
        earliest = datetime.now(tz=UTC) + self.config.min_lead_time
        return max(start, earliest)

    @override
    def hold(self, job_id: str) -> None:
        run_slurm(["scontrol", "hold", job_id], check=True)

    @override
    def release(self, job_id: str, start: datetime) -> None:
        effective = self._effective_start(start).astimezone(UTC)
        begin_str = effective.strftime("%Y-%m-%dT%H:%M:%S")  # UTC, see run_slurm
        run_slurm(
            ["scontrol", "update", f"JobId={job_id}", f"StartTime={begin_str}"],
            check=True,
        )

    @override
    def cancel(self, job_id: str) -> None:
        run_slurm(["scancel", job_id], check=False)

    def begin_time(self, job_id: str) -> datetime | None:
        """Return the begin time Slurm has recorded for a job, or None.

        This is what release() sets: ``scontrol update StartTime=`` changes
        the job's begin time, shown as ``EligibleTime``. The ``StartTime``
        shown for a pending job is Slurm's estimate, which is later than the
        begin time when the job has to wait for resources.

        Slurm prints it without a time zone; it is UTC because run_slurm runs
        scontrol with ``TZ=UTC``.
        """
        result = run_slurm(
            ["scontrol", "show", "job", job_id], capture_output=True, check=True
        )
        m = re.search(
            r"EligibleTime=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", result.stdout
        )
        if m:
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=UTC
            )
        return None
