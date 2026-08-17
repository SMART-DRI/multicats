"""Fixtures for e2e tests. These run inside the slurmctld container."""

import shutil
import subprocess

import pytest


def _slurm_available() -> bool:
    return shutil.which("sbatch") is not None and shutil.which("squeue") is not None


def _sbatch(time_limit: str, ntasks: int, duration_secs: int) -> str:
    """Submit a sleep job and return the job ID."""
    result = subprocess.run(
        [
            "sbatch",
            f"--time={time_limit}",
            f"--ntasks={ntasks}",
            "--parsable",
            "--wrap",
            f"sleep {duration_secs}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().split(";")[0]


def _scancel(job_id: str) -> None:
    subprocess.run(["scancel", job_id], check=False)


@pytest.fixture(scope="session", autouse=True)
def require_slurm() -> None:
    if not _slurm_available():
        pytest.skip("Slurm not available — run inside slurmctld container")


# (job_id, expected_time_limit_minutes, ntasks)
FAKE_JOBS = [
    ("5min-1cpu", "0:05:00", 1, 5 * 60),
    ("30min-2cpu", "0:30:00", 2, 30 * 60),
    ("2hr-4cpu", "2:00:00", 4, 2 * 3600),
]


@pytest.fixture(scope="session")
def submitted_jobs() -> list[tuple[str, str, int]]:
    """Submit fake jobs and return (job_id, time_limit_str, ntasks) tuples.

    Jobs are long sleep commands so they stay PENDING or RUNNING for the
    duration of the test session.
    """
    ids = []
    for label, time_limit, ntasks, sleep_secs in FAKE_JOBS:
        job_id = _sbatch(time_limit, ntasks, sleep_secs)
        ids.append((job_id, time_limit, ntasks))

    yield ids

    for job_id, _, _ in ids:
        _scancel(job_id)
