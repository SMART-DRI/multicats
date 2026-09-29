from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

import pytest
from cats.forecast import PointEstimate, Timeseries
from cats.providers.base import BaseProvider
from typing_extensions import override

from multicats.models import JobParams
from multicats.schedulers.coast import (
    CoastConfig,
    CoastScheduler,
    calibrate,
    slot_origin,
)

NOW = datetime(2025, 1, 1, 12, 7, tzinfo=UTC)  # current slot starts 12:00
HR: Final[timedelta] = timedelta(hours=1)
SLOT: Final[timedelta] = timedelta(minutes=30)


class FakeProvider(BaseProvider):
    BASE_URL: ClassVar[str] = "https://example.invalid"

    def __init__(self, start: datetime, values: list[float], step: timedelta = SLOT):
        super().__init__()
        self.points: list[PointEstimate] = [
            PointEstimate(value=v, datetime=start + i * step)
            for i, v in enumerate(values)
        ]

    @override
    def validate_location(self, location: str | None) -> str:
        return location or ""

    @override
    def get_max_duration_minutes(self, metric: str | None = None) -> int:
        return 2820

    @override
    def get_temporal_resolution_minutes(self, metric: str | None = None) -> int:
        return 30

    @override
    def get_data(
        self,
        timestamp: datetime,
        location: str | None = None,
        metric: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Timeseries:
        return Timeseries("Carbon intensity", values=self.points, unit="gCO2eq/kWh")


def make_job(
    job_id: str = "1",
    arrival: datetime = NOW - timedelta(minutes=2),
    duration: timedelta = HR,
    resource: float = 4.0,
    max_wait: timedelta = 12 * HR,
) -> JobParams:
    return JobParams(
        job_id=job_id,
        arrival=arrival,
        resource=resource,
        duration=duration,
        energy=0.04 * resource * duration / HR,
        deadline=arrival + max_wait + duration,
        max_wait=max_wait,
    )


def scheduler(values: list[float], **config: float) -> CoastScheduler:
    origin = slot_origin(NOW, SLOT)
    return CoastScheduler(
        CoastConfig(**config),  # pyright: ignore[reportArgumentType]
        carbon=FakeProvider(origin, values),
        clock=lambda: NOW,
    )


def test_slot_origin_aligns_to_utc_half_hours() -> None:
    assert slot_origin(NOW, SLOT) == datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    exact = datetime(2025, 1, 1, 12, 30, tzinfo=UTC)
    assert slot_origin(exact, SLOT) == exact


def test_empty_job_list() -> None:
    assert CoastScheduler().assign([], 0.0) == {}


def test_requires_carbon_provider() -> None:
    with pytest.raises(ValueError, match="carbon-intensity provider"):
        _ = CoastScheduler().assign([make_job()], 0.0)


def test_starts_on_slot_boundaries_after_now() -> None:
    result = scheduler([100.0] * 96).assign([make_job()], 0.0)
    start = result["1"]
    assert start >= NOW
    assert (start - slot_origin(NOW, SLOT)) % SLOT == timedelta(0)


def test_waits_for_low_carbon_slot() -> None:
    carbon = [300.0] * 96
    carbon[6] = 50.0  # 15:00-15:30
    job = make_job(duration=timedelta(minutes=30))
    result = scheduler(carbon, delay_weight=0.0, congestion_weight=0.0).assign(
        [job], 0.0
    )
    assert result["1"] == datetime(2025, 1, 1, 15, 0, tzinfo=UTC)


def test_carbon_counts_every_occupied_slot() -> None:
    # slot 2 alone is cheapest, but a 1 h job starting at slot 4 is cheaper
    carbon = [300.0, 300.0, 10.0, 400.0, 60.0, 60.0] + [300.0] * 90
    result = scheduler(carbon, delay_weight=0.0, congestion_weight=0.0).assign(
        [make_job(duration=HR)], 0.0
    )
    assert result["1"] == datetime(2025, 1, 1, 14, 0, tzinfo=UTC)


def test_congestion_spreads_identical_jobs() -> None:
    jobs = [make_job(str(i), duration=timedelta(minutes=30)) for i in range(4)]
    flat = [100.0] * 96
    greedy = scheduler(flat, delay_weight=0.0, congestion_weight=0.0).assign(jobs, 0.0)
    coast = scheduler(flat, delay_weight=0.0, congestion_weight=1.0).assign(jobs, 0.0)
    assert len(set(greedy.values())) == 1
    assert len(set(coast.values())) == len(jobs)


def test_respects_max_wait() -> None:
    carbon = [300.0] * 96
    carbon[20] = 1.0  # far beyond the job's max wait
    job = make_job(duration=timedelta(minutes=30), max_wait=2 * HR)
    start = scheduler(carbon, delay_weight=0.0).assign([job], 0.0)["1"]
    assert job.is_feasible(start)


def test_job_without_feasible_slot_starts_now() -> None:
    job = make_job(duration=72 * HR)  # longer than the forecast
    assert scheduler([100.0] * 96).assign([job], 0.0)["1"] == NOW


def test_background_load_scalar_does_not_change_decisions() -> None:
    carbon = [float(100 + (7 * k) % 50) for k in range(96)]
    jobs = [make_job(str(i), resource=8.0 * (i + 1)) for i in range(5)]
    sched = scheduler(carbon)
    assert sched.assign(jobs, 0.0) == sched.assign(jobs, 0.9)


def test_averages_finer_forecast_into_slots() -> None:
    origin = slot_origin(NOW, SLOT)
    # 15-minute points: slot 1 averages (300, 20) = 160, slot 2 is (90, 90)
    values = [200.0, 200.0, 300.0, 20.0, 90.0, 90.0] + [200.0] * 186
    sched = CoastScheduler(
        CoastConfig(delay_weight=0.0, congestion_weight=0.0),
        carbon=FakeProvider(origin, values, step=timedelta(minutes=15)),
        clock=lambda: NOW,
    )
    job = make_job(duration=timedelta(minutes=30))
    assert sched.assign([job], 0.0)["1"] == origin + 2 * SLOT


def test_forecast_starting_next_slot_shifts_origin() -> None:
    next_slot = slot_origin(NOW, SLOT) + SLOT
    sched = CoastScheduler(
        CoastConfig(), carbon=FakeProvider(next_slot, [100.0] * 95), clock=lambda: NOW
    )
    assert sched.assign([make_job()], 0.0)["1"] >= next_slot


@pytest.mark.parametrize(
    "kwargs",
    [
        {"carbon_weight": -1.0},
        {"carbon_ref": 0.0},
        {"delay_ref": timedelta(0)},
        {"slot_duration": timedelta(seconds=90)},
        {"horizon": timedelta(minutes=45)},
        {"max_sweeps": 0},
    ],
)
def test_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _ = CoastConfig(**kwargs)  # pyright: ignore[reportArgumentType]


def test_calibrate_uses_medians() -> None:
    jobs = [
        make_job("a", duration=timedelta(minutes=30), resource=2.0),
        make_job("b", duration=HR, resource=4.0),
        make_job("c", duration=2 * HR, resource=8.0),
    ]
    cfg = calibrate(CoastConfig(), jobs, mean_carbon=100.0)
    # energies 0.04, 0.16, 0.64 kWh; r^2 * slots = 4, 32, 256
    assert cfg.carbon_ref == pytest.approx(16.0)
    assert cfg.congestion_ref == pytest.approx(32.0)
    assert cfg.carbon_weight == CoastConfig().carbon_weight
