"""E2e tests for CoastScheduler against a real Slurm cluster.

The solver itself is covered by tests/test_coast_scheduler.py and
tests/test_coast_reference.py. These tests check the seams around it: jobs as
SlurmCLIProvider builds them from squeue, and start times as SlurmController
applies them. Carbon intensity comes from a fixed fake forecast so the tests
do not depend on a live API.
"""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from multicats.controllers.slurm import SlurmController, SlurmControllerConfig
from multicats.models import JobParams
from multicats.providers.slurm_cli import SlurmCLIConfig, SlurmCLIProvider
from multicats.schedulers.coast import CoastConfig, CoastScheduler, slot_origin

from tests.e2e.conftest import submit_held_jobs
from tests.fakes import FakeProvider

pytestmark = pytest.mark.e2e

SLOT = timedelta(minutes=30)
HIGH, LOW = 400.0, 50.0
DIP_SLOTS = range(4, 6)  # 2-3h out: never clamped by the controller


@pytest.fixture(scope="module")
def provider() -> SlurmCLIProvider:
    return SlurmCLIProvider(SlurmCLIConfig(default_max_wait=timedelta(hours=24)))


@pytest.fixture(scope="module")
def controller() -> SlurmController:
    return SlurmController(SlurmControllerConfig(min_lead_time=timedelta(minutes=1)))


@pytest.fixture(scope="module")
def now() -> datetime:
    return datetime.now(tz=UTC)


@pytest.fixture(scope="module")
def origin(now: datetime) -> datetime:
    return slot_origin(now, SLOT)


def _forecast(origin: datetime, dip: bool) -> FakeProvider:
    values = [HIGH] * 96
    if dip:
        for k in DIP_SLOTS:
            values[k] = LOW
    return FakeProvider(origin, values)


def _scheduler(
    origin: datetime, now: datetime, *, dip: bool = True, **config: float
) -> CoastScheduler:
    return CoastScheduler(
        _forecast(origin, dip),
        CoastConfig(**config),  # pyright: ignore[reportArgumentType]
        clock=lambda: now,
    )


def _pending(provider: SlurmCLIProvider, job_ids: list[str]) -> list[JobParams]:
    pending = {j.job_id: j for j in provider.pending_jobs()}
    missing = set(job_ids) - set(pending)
    assert not missing, f"Jobs not found in pending queue: {missing}"
    return [pending[jid] for jid in job_ids]


def _apply(
    assignments: Mapping[str, datetime | None], controller: SlurmController
) -> None:
    """Release assigned jobs; rejected (None) jobs stay held."""
    for job_id, start in assignments.items():
        if start is not None:
            controller.release(job_id, start)


def _held(
    n: int, controller: SlurmController, **kwargs: str | int
) -> Iterator[list[str]]:
    job_ids = submit_held_jobs(n, **kwargs)  # pyright: ignore[reportArgumentType]
    yield job_ids
    for job_id in job_ids:
        controller.cancel(job_id)


@pytest.fixture(scope="module")
def held_jobs(controller: SlurmController) -> Iterator[list[str]]:
    yield from _held(6, controller)


@pytest.fixture(scope="module")
def two_cpu_jobs(controller: SlurmController) -> Iterator[list[str]]:
    yield from _held(4, controller, ntasks=2)


@pytest.fixture(scope="module")
def long_job(controller: SlurmController) -> Iterator[list[str]]:
    # 72 h: longer than the 48 h forecast, so COAST has no feasible slot
    yield from _held(1, controller, time_limit="3-00:00:00")


@pytest.fixture(scope="module")
def jobs(provider: SlurmCLIProvider, held_jobs: list[str]) -> list[JobParams]:
    return _pending(provider, held_jobs)


@pytest.fixture(scope="module")
def assignments(
    provider: SlurmCLIProvider,
    jobs: list[JobParams],
    origin: datetime,
    now: datetime,
) -> Mapping[str, datetime | None]:
    return _scheduler(origin, now).assign(jobs, provider.background_load())


def test_every_held_job_gets_a_start(
    jobs: list[JobParams], assignments: Mapping[str, datetime | None]
) -> None:
    assert set(assignments) == {j.job_id for j in jobs}
    rejected = [jid for jid, start in assignments.items() if start is None]
    assert not rejected, f"COAST rejected jobs: {rejected}"


def test_starts_feasible_and_slot_aligned(
    jobs: list[JobParams],
    assignments: Mapping[str, datetime | None],
    origin: datetime,
    now: datetime,
) -> None:
    for job in jobs:
        start = assignments[job.job_id]
        assert start is not None
        assert job.is_feasible(start), (
            f"Job {job.job_id}: start {start} outside "
            f"[{job.earliest_start()}, {job.latest_start()}]"
        )
        assert start >= now
        assert (start - origin) % SLOT == timedelta(0), (
            f"Job {job.job_id}: start {start} not on a {SLOT} slot boundary"
        )


def test_start_times_applied_to_slurm(
    controller: SlurmController,
    assignments: Mapping[str, datetime | None],
) -> None:
    # Snapshot the floor before releasing so the expected values match what
    # the controller computes internally.
    floor = datetime.now(tz=UTC) + controller.config.min_lead_time
    _apply(assignments, controller)

    # Slurm rounds to the minute — allow ±60s tolerance.
    for job_id, start in assignments.items():
        assert start is not None
        expected = max(start, floor)
        actual = controller.start_time(job_id)
        assert actual is not None, f"Job {job_id} has no start time in Slurm"
        diff = abs((actual - expected).total_seconds())
        assert diff <= 60, (
            f"Job {job_id}: Slurm start {actual} differs from "
            f"applied {expected} by {diff:.0f}s"
        )


def test_congestion_spreads_jobs(
    provider: SlurmCLIProvider,
    two_cpu_jobs: list[str],
    origin: datetime,
    now: datetime,
) -> None:
    jobs = _pending(provider, two_cpu_jobs)
    assert all(j.resource == 2 for j in jobs)
    load = provider.background_load()

    greedy = _scheduler(
        origin, now, dip=False, delay_weight=0.0, congestion_weight=0.0
    ).assign(jobs, load)
    coast = _scheduler(
        origin, now, dip=False, delay_weight=0.0, congestion_weight=1.0
    ).assign(jobs, load)

    assert len(set(greedy.values())) == 1
    assert len(set(coast.values())) == len(jobs)
    assert None not in coast.values()


def test_rejected_job_is_not_released(
    provider: SlurmCLIProvider,
    controller: SlurmController,
    held_jobs: list[str],
    long_job: list[str],
    origin: datetime,
    now: datetime,
) -> None:
    [long_id] = long_job
    batch = _pending(provider, [*held_jobs, long_id])
    result = _scheduler(origin, now).assign(batch, provider.background_load())

    assert result[long_id] is None
    assert all(result[jid] is not None for jid in held_jobs)

    _apply({long_id: result[long_id]}, controller)
    # still held by job_submit.lua, a year out
    start = controller.start_time(long_id)
    assert start is None or start > now + timedelta(days=300), (
        f"Rejected job {long_id} has start time {start}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="SlurmCLIProvider estimates zero energy for pending jobs, "
    + "so the carbon term vanishes",
)
def test_carbon_steers_to_dip(
    provider: SlurmCLIProvider,
    jobs: list[JobParams],
    origin: datetime,
    now: datetime,
) -> None:
    result = _scheduler(origin, now, delay_weight=0.0, congestion_weight=0.0).assign(
        jobs, provider.background_load()
    )
    dip = {origin + k * SLOT for k in DIP_SLOTS}
    for job_id, start in result.items():
        assert start in dip, f"Job {job_id}: start {start} not in low-carbon dip"
