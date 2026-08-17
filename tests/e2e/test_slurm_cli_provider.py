"""E2e tests for SlurmCLIProvider against a real Slurm cluster.

Run inside the slurmctld container after ./cluster/install_multicats.sh.
"""

from datetime import timedelta

import pytest

from multicats.providers.slurm_cli import SlurmCLIConfig, SlurmCLIProvider

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def provider() -> SlurmCLIProvider:
    return SlurmCLIProvider(SlurmCLIConfig(default_max_wait=timedelta(hours=24)))


def test_background_load_is_fraction(provider: SlurmCLIProvider) -> None:
    load = provider.background_load()
    assert 0.0 <= load <= 1.0


def test_pending_jobs_returns_submitted(
    provider: SlurmCLIProvider,
    submitted_jobs: list[tuple[str, str, int]],
) -> None:
    submitted_ids = {job_id for job_id, _, _ in submitted_jobs}
    pending = provider.pending_jobs()
    pending_ids = {job.job_id for job in pending}
    assert pending_ids  # at least one pending job
    # All submitted jobs should appear as pending (or running — filter both)
    assert submitted_ids.issubset(pending_ids), (
        f"Missing jobs: {submitted_ids - pending_ids}"
    )


def test_pending_jobs_have_correct_durations(
    provider: SlurmCLIProvider,
    submitted_jobs: list[tuple[str, str, int]],
) -> None:
    # Map expected minutes from FAKE_JOBS time-limit strings
    expected_minutes = {}
    for job_id, time_limit_str, _ in submitted_jobs:
        h, m, _s = time_limit_str.split(":")
        expected_minutes[job_id] = int(h) * 60 + int(m)

    pending = {job.job_id: job for job in provider.pending_jobs()}

    for job_id, expected_mins in expected_minutes.items():
        assert job_id in pending, f"Job {job_id} not in pending list"
        actual = pending[job_id].duration
        assert actual == timedelta(minutes=expected_mins), (
            f"Job {job_id}: expected {expected_mins}min, got {actual}"
        )


def test_pending_jobs_have_correct_resource_counts(
    provider: SlurmCLIProvider,
    submitted_jobs: list[tuple[str, str, int]],
) -> None:
    expected_ntasks = {job_id: ntasks for job_id, _, ntasks in submitted_jobs}
    pending = {job.job_id: job for job in provider.pending_jobs()}

    for job_id, ntasks in expected_ntasks.items():
        assert job_id in pending
        assert pending[job_id].resource == ntasks, (
            f"Job {job_id}: expected {ntasks} CPUs, got {pending[job_id].resource}"
        )


def test_pending_jobs_feasibility_windows_are_valid(
    provider: SlurmCLIProvider,
    submitted_jobs: list[tuple[str, str, int]],
) -> None:
    submitted_ids = {job_id for job_id, _, _ in submitted_jobs}
    pending = {job.job_id: job for job in provider.pending_jobs()}

    for job_id in submitted_ids:
        if job_id not in pending:
            continue
        job = pending[job_id]
        assert job.earliest_start() <= job.latest_start(), (
            f"Job {job_id} has infeasible window: "
            f"{job.earliest_start()} > {job.latest_start()}"
        )


def test_energy_estimate_returns_non_negative(
    provider: SlurmCLIProvider,
    submitted_jobs: list[tuple[str, str, int]],
) -> None:
    for job_id, _, _ in submitted_jobs:
        energy = provider.energy_estimate(job_id)
        assert energy >= 0.0, f"Negative energy estimate for job {job_id}"
