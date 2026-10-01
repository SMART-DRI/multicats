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
    _carbon_slots,
    calibrate,
    slot_origin,
)

NOW = datetime(2025, 1, 1, 12, 7, tzinfo=UTC)  # current slot starts 12:00
HR: Final[timedelta] = timedelta(hours=1)
SLOT: Final[timedelta] = timedelta(minutes=30)
ORIGIN: Final[datetime] = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
ARRIVAL: Final[datetime] = NOW - timedelta(minutes=2)


class FakeProvider(BaseProvider):
    BASE_URL: ClassVar[str] = "https://example.invalid"

    def __init__(
        self, start: datetime, values: list[float], resolution: int = 30
    ) -> None:
        super().__init__()
        self.resolution: int = resolution
        step = timedelta(minutes=resolution)
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
        return self.resolution

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
    arrival: datetime = ARRIVAL,
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
    return CoastScheduler(
        FakeProvider(ORIGIN, values),
        CoastConfig(**config),  # pyright: ignore[reportArgumentType]
        clock=lambda: NOW,
    )


def test_slot_origin_aligns_to_utc_half_hours() -> None:
    assert slot_origin(NOW, SLOT) == ORIGIN
    exact = datetime(2025, 1, 1, 12, 30, tzinfo=UTC)
    assert slot_origin(exact, SLOT) == exact


# construction


def test_requires_carbon_provider() -> None:
    with pytest.raises(TypeError, match="carbon-intensity provider"):
        _ = CoastScheduler(None)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(("resolution", "expected"), [(30, 30), (15, 15)])
def test_slot_length_defaults_to_forecast_resolution(
    resolution: int, expected: int
) -> None:
    sched = CoastScheduler(FakeProvider(ORIGIN, [100.0] * 4, resolution))
    assert sched.config.slot_minutes == expected


def test_slot_length_can_be_a_multiple_of_the_resolution() -> None:
    provider = FakeProvider(ORIGIN, [100.0] * 4, resolution=15)
    sched = CoastScheduler(provider, CoastConfig(slot_minutes=60))
    assert sched.config.slot_minutes == 60


@pytest.mark.parametrize("slot_minutes", [15, 45])
def test_slot_length_must_be_a_multiple_of_the_resolution(slot_minutes: int) -> None:
    with pytest.raises(ValueError, match="multiple of the carbon forecast"):
        _ = CoastScheduler(
            FakeProvider(ORIGIN, [100.0] * 4), CoastConfig(slot_minutes=slot_minutes)
        )


def test_horizon_must_be_a_multiple_of_the_resolved_slot() -> None:
    with pytest.raises(ValueError, match="horizon"):
        _ = CoastScheduler(
            FakeProvider(ORIGIN, [100.0] * 4),
            CoastConfig(horizon=timedelta(hours=1.25)),
        )


# assign()


def test_empty_job_list() -> None:
    assert scheduler([100.0] * 96).assign([], 0.0) == {}


def test_starts_on_slot_boundaries_after_now() -> None:
    start = scheduler([100.0] * 96).assign([make_job()], 0.0)["1"]
    assert start is not None
    assert start >= NOW
    assert (start - ORIGIN) % SLOT == timedelta(0)


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
    assert start is not None
    assert job.is_feasible(start)


def test_job_without_feasible_slot_is_rejected() -> None:
    too_long = make_job("long", duration=72 * HR)  # longer than the forecast
    ok = make_job("ok")
    result = scheduler([100.0] * 96).assign([too_long, ok], 0.0)
    assert result["long"] is None
    assert result["ok"] is not None


def test_background_load_scalar_does_not_change_decisions() -> None:
    carbon = [float(100 + (7 * k) % 50) for k in range(96)]
    jobs = [make_job(str(i), resource=8.0 * (i + 1)) for i in range(5)]
    sched = scheduler(carbon)
    assert sched.assign(jobs, 0.0) == sched.assign(jobs, 0.9)


def test_uses_coarser_slots_on_finer_forecast() -> None:
    # 15-min points averaged into 30-min slots: slot 1 = (300 + 20) / 2 = 160,
    # slot 2 = (90 + 90) / 2 = 90, so the 30-min job goes to slot 2
    values = [200.0, 200.0, 300.0, 20.0, 90.0, 90.0] + [200.0] * 186
    sched = CoastScheduler(
        FakeProvider(ORIGIN, values, resolution=15),
        CoastConfig(slot_minutes=30, delay_weight=0.0, congestion_weight=0.0),
        clock=lambda: NOW,
    )
    job = make_job(duration=timedelta(minutes=30))
    assert sched.assign([job], 0.0)["1"] == ORIGIN + 2 * SLOT


# _carbon_slots


def config(slot_minutes: int = 30) -> CoastConfig:
    return CoastConfig(slot_minutes=slot_minutes)


def test_carbon_slots_aligned_forecast() -> None:
    provider = FakeProvider(ORIGIN, [10.0, 20.0, 30.0])
    assert _carbon_slots(provider, NOW, config()) == (ORIGIN, [10.0, 20.0, 30.0])


def test_carbon_slots_averages_points_within_a_slot() -> None:
    provider = FakeProvider(ORIGIN, [10.0, 30.0, 50.0, 70.0], resolution=15)
    assert _carbon_slots(provider, NOW, config()) == (ORIGIN, [20.0, 60.0])


def test_carbon_slots_forecast_starting_next_slot_shifts_origin() -> None:
    provider = FakeProvider(ORIGIN + SLOT, [10.0, 20.0])
    assert _carbon_slots(provider, NOW, config()) == (ORIGIN + SLOT, [10.0, 20.0])


def test_carbon_slots_ignores_points_before_current_slot() -> None:
    provider = FakeProvider(ORIGIN - 2 * SLOT, [999.0, 999.0, 10.0, 20.0])
    assert _carbon_slots(provider, NOW, config()) == (ORIGIN, [10.0, 20.0])


def test_carbon_slots_stops_at_first_gap() -> None:
    provider = FakeProvider(ORIGIN, [10.0, 20.0, 30.0, 40.0])
    _ = provider.points.pop(2)  # no data for slot 2
    assert _carbon_slots(provider, NOW, config()) == (ORIGIN, [10.0, 20.0])


def test_carbon_slots_empty_forecast_raises() -> None:
    provider = FakeProvider(ORIGIN - 2 * SLOT, [10.0, 20.0])  # all in the past
    with pytest.raises(ValueError, match="no data"):
        _ = _carbon_slots(provider, NOW, config())


# configuration


@pytest.mark.parametrize(
    "kwargs",
    [
        {"carbon_weight": -1.0},
        {"carbon_ref": 0.0},
        {"delay_ref": timedelta(0)},
        {"slot_minutes": 0},
        {"horizon": timedelta(0)},
        {"slot_minutes": 30, "horizon": timedelta(minutes=45)},
        {"max_sweeps": 0},
    ],
)
def test_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _ = CoastConfig(**kwargs)  # pyright: ignore[reportArgumentType]


def test_unresolved_slot_length_raises() -> None:
    with pytest.raises(ValueError, match="slot_minutes is not set"):
        _ = CoastConfig().slot_duration


def test_calibrate_uses_medians() -> None:
    jobs = [
        make_job("a", duration=timedelta(minutes=30), resource=2.0),
        make_job("b", duration=HR, resource=4.0),
        make_job("c", duration=2 * HR, resource=8.0),
    ]
    cfg = calibrate(config(), jobs, mean_carbon=100.0)
    # energies 0.04, 0.16, 0.64 kWh; r^2 * slots = 4, 32, 256
    assert cfg.carbon_ref == pytest.approx(16.0)  # gCO2
    assert cfg.congestion_ref == pytest.approx(32.0)
    assert cfg.carbon_weight == CoastConfig().carbon_weight
