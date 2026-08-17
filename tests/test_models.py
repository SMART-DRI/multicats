from datetime import UTC, datetime, timedelta

from multicats.models import JobParams

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def make_job(**kwargs: object) -> JobParams:
    defaults: dict[str, object] = {
        "job_id": "1",
        "arrival": NOW,
        "resource": 4.0,
        "duration": timedelta(hours=1),
        "energy": 1.0,
        "deadline": NOW + timedelta(hours=6),
        "max_wait": timedelta(hours=3),
    }
    defaults.update(kwargs)
    return JobParams(**defaults)  # type: ignore[arg-type]


def test_earliest_start_is_arrival() -> None:
    job = make_job()
    assert job.earliest_start() == NOW


def test_latest_start_bounded_by_max_wait() -> None:
    job = make_job(
        deadline=NOW + timedelta(hours=10),
        max_wait=timedelta(hours=2),
        duration=timedelta(hours=1),
    )
    assert job.latest_start() == NOW + timedelta(hours=2)


def test_latest_start_bounded_by_deadline() -> None:
    job = make_job(
        deadline=NOW + timedelta(hours=3),
        max_wait=timedelta(hours=10),
        duration=timedelta(hours=2),
    )
    # deadline - duration = NOW+1h, which is less than arrival+max_wait
    assert job.latest_start() == NOW + timedelta(hours=1)


def test_is_feasible_within_window() -> None:
    job = make_job()
    assert job.is_feasible(NOW)
    assert job.is_feasible(NOW + timedelta(hours=1))


def test_is_feasible_rejects_before_arrival() -> None:
    job = make_job()
    assert not job.is_feasible(NOW - timedelta(seconds=1))


def test_is_feasible_rejects_after_latest_start() -> None:
    job = make_job(max_wait=timedelta(hours=2), duration=timedelta(hours=1))
    assert not job.is_feasible(NOW + timedelta(hours=3))
