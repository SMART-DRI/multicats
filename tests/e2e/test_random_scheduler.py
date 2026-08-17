"""E2e tests for RandomScheduler against a real Slurm cluster.

Jobs are submitted to the "multicats" partition, where the job_submit.lua
plugin holds them by setting BeginTime one year in the future.
The scheduler assigns start times; SlurmController applies them; the test
verifies Slurm reflects the correct StartTime.
"""

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from multicats.controllers.slurm import SlurmController, SlurmControllerConfig
from multicats.providers.slurm_cli import SlurmCLIConfig, SlurmCLIProvider
from multicats.schedulers.random import RandomConfig, RandomScheduler

pytestmark = pytest.mark.e2e

NUM_JOBS = 10
WINDOW = timedelta(minutes=5)


@pytest.fixture(scope="module")
def provider() -> SlurmCLIProvider:
    return SlurmCLIProvider(SlurmCLIConfig(default_max_wait=timedelta(hours=24)))


@pytest.fixture(scope="module")
def controller() -> SlurmController:
    return SlurmController(SlurmControllerConfig(min_lead_time=timedelta(minutes=1)))


@pytest.fixture(scope="module")
def held_jobs(controller: SlurmController) -> list[str]:
    """Submit NUM_JOBS to the multicats partition; cancel on teardown."""
    job_ids = []
    for _ in range(NUM_JOBS):
        result = subprocess.run(
            [
                "sbatch",
                "--partition=multicats",
                "--time=0:30:00",
                "--ntasks=1",
                "--parsable",
                "--wrap",
                "sleep 1800",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        job_ids.append(result.stdout.strip().split(";")[0])

    yield job_ids

    for job_id in job_ids:
        controller.cancel(job_id)


@pytest.fixture(scope="module")
def assignments(
    provider: SlurmCLIProvider,
    held_jobs: list[str],
) -> dict[str, datetime]:
    pending = {j.job_id: j for j in provider.pending_jobs()}
    jobs = [pending[jid] for jid in held_jobs if jid in pending]

    assert len(jobs) >= NUM_JOBS, (
        f"Expected {NUM_JOBS} pending jobs, got {len(jobs)}. "
        f"Missing: {set(held_jobs) - set(pending)}"
    )

    scheduler = RandomScheduler(RandomConfig(window=WINDOW, seed=42))
    return scheduler.assign(jobs, provider.background_load())


def test_all_jobs_are_pending(
    provider: SlurmCLIProvider,
    held_jobs: list[str],
) -> None:
    pending_ids = {j.job_id for j in provider.pending_jobs()}
    missing = set(held_jobs) - pending_ids
    assert not missing, f"Jobs not found in pending queue: {missing}"


def test_assigned_start_times_within_window(
    provider: SlurmCLIProvider,
    held_jobs: list[str],
    assignments: dict[str, datetime],
) -> None:
    pending = {j.job_id: j for j in provider.pending_jobs()}
    for job_id, start in assignments.items():
        job = pending[job_id]
        assert job.earliest_start() <= start, (
            f"Job {job_id}: start {start} before arrival {job.earliest_start()}"
        )
        assert start <= job.arrival + WINDOW, (
            f"Job {job_id}: start {start} exceeds 5-minute window "
            f"(arrival={job.arrival}, limit={job.arrival + WINDOW})"
        )


def test_start_times_applied_to_slurm(
    controller: SlurmController,
    held_jobs: list[str],
    assignments: dict[str, datetime],
) -> None:
    # Snapshot the effective floor before releasing, so our expected values
    # match what the controller will compute internally.
    floor = datetime.now(tz=UTC) + controller.config.min_lead_time

    for job_id, start in assignments.items():
        controller.release(job_id, start)

    # Slurm rounds to the minute — allow ±60s tolerance.
    for job_id, start in assignments.items():
        expected = max(start, floor)
        actual = controller.start_time(job_id)
        assert actual is not None, f"Job {job_id} has no start time in Slurm"
        diff = abs((actual - expected).total_seconds())
        assert diff <= 60, (
            f"Job {job_id}: Slurm start {actual} differs from "
            f"applied {expected} by {diff:.0f}s"
        )
