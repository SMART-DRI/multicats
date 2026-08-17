import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from typing_extensions import override

from ..interfaces import Controller


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
        subprocess.run(["scontrol", "hold", job_id], check=True)

    @override
    def release(self, job_id: str, start: datetime) -> None:
        effective = self._effective_start(start)
        begin_str = effective.strftime("%Y-%m-%dT%H:%M:%S")
        subprocess.run(
            ["scontrol", "update", f"JobId={job_id}", f"StartTime={begin_str}"],
            check=True,
        )

    @override
    def cancel(self, job_id: str) -> None:
        subprocess.run(["scancel", job_id], check=False)

    def start_time(self, job_id: str) -> datetime | None:
        """Return the StartTime Slurm has recorded for a job, or None."""
        result = subprocess.run(
            ["scontrol", "show", "job", job_id],
            capture_output=True,
            text=True,
            check=True,
        )
        m = re.search(r"StartTime=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", result.stdout)
        if m:
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=UTC
            )
        return None
