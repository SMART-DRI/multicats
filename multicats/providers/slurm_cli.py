import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from typing_extensions import override

from ..interfaces import QueueProvider
from ..models import JobParams

SlurmObj = dict[str, Any]


def _ts(value: SlurmObj | int) -> datetime | None:
    """Parse a Slurm timestamp: plain int (Unix epoch) or {set, number, infinite} object."""
    if isinstance(value, int):
        return datetime.fromtimestamp(value, tz=UTC) if value > 0 else None
    if not value.get("set") or value.get("infinite"):
        return None
    return datetime.fromtimestamp(int(value["number"]), tz=UTC)


def _minutes(value: SlurmObj | int) -> timedelta | None:
    """Parse a Slurm time-limit: plain int (minutes) or {set, number, infinite} object."""
    if isinstance(value, int):
        return timedelta(minutes=value) if value > 0 else None
    if not value.get("set") or value.get("infinite"):
        return None
    return timedelta(minutes=int(value["number"]))


def _cpus(value: SlurmObj | int) -> int:
    """Parse a Slurm CPU count: plain int or {number, ...} object."""
    if isinstance(value, int):
        return value
    return int(value.get("number", 1))


@dataclass
class SlurmCLIConfig:
    default_max_wait: timedelta = timedelta(hours=24)
    watts_per_cpu: float = 10.0  # used when sacct has no energy data


class SlurmCLIProvider(QueueProvider):
    def __init__(self, config: SlurmCLIConfig | None = None) -> None:
        self.config: SlurmCLIConfig = config or SlurmCLIConfig()

    def _run(self, *args: str) -> SlurmObj:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)  # type: ignore[no-any-return]

    @override
    def background_load(self) -> float:
        """Fraction of cluster CPUs currently allocated to running jobs."""
        data = self._run("sinfo", "--json")
        total = allocated = 0
        for partition in data.get("sinfo", []):
            cpus = partition.get("cpus", {})
            total += cpus.get("total", 0)
            allocated += cpus.get("allocated", 0)
        return allocated / total if total else 0.0

    @override
    def pending_jobs(self) -> list[JobParams]:
        data = self._run("squeue", "--json", "--states=PENDING")
        jobs = []
        for j in data.get("jobs", []):
            arrival = _ts(j.get("submit_time", {}))
            duration = _minutes(j.get("time_limit", {}))
            deadline = _ts(j.get("deadline", {}))

            if arrival is None or duration is None:
                continue

            if deadline is None:
                deadline = arrival + self.config.default_max_wait + duration

            cpus = _cpus(j.get("cpus", 1))
            energy = self.energy_estimate(str(j["job_id"]))

            jobs.append(
                JobParams(
                    job_id=str(j["job_id"]),
                    arrival=arrival,
                    resource=cpus,
                    duration=duration,
                    energy=energy,
                    deadline=deadline,
                    max_wait=self.config.default_max_wait,
                )
            )
        return jobs

    @override
    def energy_estimate(self, job_id: str) -> float:
        """Estimate energy in kWh from sacct; fall back to a CPU-wattage heuristic."""
        try:
            data = self._run(
                "sacct",
                "--json",
                f"--jobs={job_id}",
                "--format=JobID,CPUTimeRAW,ConsumedEnergyRaw",
            )
            for job in data.get("jobs", []):
                raw = job.get("energy", {}).get("consumed", {})
                if raw.get("set") and raw["number"] > 0:
                    return raw["number"] / 3_600_000  # J -> kWh
            # Fall through to heuristic if sacct has no energy data
            for job in data.get("jobs", []):
                cpus = job.get("allocation_nodes", 1)
                cpu_time_hours = job.get("time", {}).get("elapsed", 0) / 3600
                return cpus * cpu_time_hours * self.config.watts_per_cpu / 1000
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError):
            pass
        return 0.0
