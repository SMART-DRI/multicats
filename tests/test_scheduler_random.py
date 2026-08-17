from datetime import UTC, datetime, timedelta
from typing import Final

from multicats.models import JobParams
from multicats.schedulers.random import RandomConfig, RandomScheduler

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
HR: Final[timedelta] = timedelta(hours=1)


def make_job(
    job_id: str = "1",
    max_wait: timedelta = 6 * HR,
    deadline_offset: timedelta = 12 * HR,
    duration: timedelta = HR,
) -> JobParams:
    return JobParams(
        job_id=job_id,
        arrival=NOW,
        resource=1.0,
        duration=duration,
        energy=1.0,
        deadline=NOW + deadline_offset,
        max_wait=max_wait,
    )


def test_assign_returns_all_jobs() -> None:
    jobs = [make_job("1"), make_job("2"), make_job("3")]
    result = RandomScheduler().assign(jobs, 0.0)
    assert set(result) == {"1", "2", "3"}


def test_assigned_time_within_feasible_window() -> None:
    job = make_job(max_wait=timedelta(hours=6), duration=timedelta(hours=1))
    scheduler = RandomScheduler()
    for _ in range(50):
        result = scheduler.assign([job], 0.0)
        start = result[job.job_id]
        assert job.is_feasible(start), f"Infeasible start: {start}"


def test_window_caps_scheduling_horizon() -> None:
    # Job has a 48h max_wait but the scheduler window is only 2h
    job = make_job(max_wait=timedelta(hours=48), deadline_offset=timedelta(hours=72))
    scheduler = RandomScheduler(RandomConfig(window=timedelta(hours=2)))
    for _ in range(50):
        result = scheduler.assign([job], 0.0)
        start = result[job.job_id]
        assert start <= NOW + timedelta(hours=2), f"Start {start} exceeds window"


def test_window_does_not_push_past_latest_start() -> None:
    # Job's latest_start is tighter than the window
    job = make_job(max_wait=timedelta(hours=1), deadline_offset=timedelta(hours=3))
    scheduler = RandomScheduler(RandomConfig(window=timedelta(hours=48)))
    for _ in range(50):
        result = scheduler.assign([job], 0.0)
        start = result[job.job_id]
        assert job.is_feasible(start), f"Infeasible start: {start}"


def test_zero_width_window_returns_earliest_start() -> None:
    # Window is so small that hi <= lo: should fall back to earliest_start
    job = make_job(max_wait=timedelta(seconds=0), deadline_offset=timedelta(hours=1))
    result = RandomScheduler(RandomConfig(window=timedelta(seconds=0))).assign(
        [job], 0.0
    )
    assert result[job.job_id] == job.earliest_start()


def test_empty_job_list() -> None:
    assert RandomScheduler().assign([], 0.0) == {}


def test_seed_produces_reproducible_results() -> None:
    jobs = [make_job(str(i)) for i in range(5)]
    s1 = RandomScheduler(RandomConfig(seed=42)).assign(jobs, 0.0)
    s2 = RandomScheduler(RandomConfig(seed=42)).assign(jobs, 0.0)
    assert s1 == s2


def test_different_seeds_produce_different_results() -> None:
    jobs = [make_job(str(i)) for i in range(5)]
    s1 = RandomScheduler(RandomConfig(seed=1)).assign(jobs, 0.0)
    s2 = RandomScheduler(RandomConfig(seed=2)).assign(jobs, 0.0)
    assert s1 != s2
