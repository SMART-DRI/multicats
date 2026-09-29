import pytest

from multicats.schedulers.coast import (
    CoastConfig,
    _best_slot,
    _carbon_cost,
    _congestion,
    _congestion_potential,
    _delay,
    _job_cost,
    _SlotJob,
)


def make_job(
    slots: int = 1, resource: float = 1.0, energy: float = 1.0, arrival: float = 0.0
) -> _SlotJob:
    return _SlotJob(
        job_id="1",
        arrival=arrival,
        runtime=30 * slots,
        slots=slots,
        resource=resource,
        energy=energy,
        max_wait=24 * 60.0,
        deadline=arrival + 24 * 60.0 + 30 * slots,
    )


def test_congestion_potential_is_quadratic() -> None:
    assert _congestion_potential(0.0) == 0.0
    assert _congestion_potential(2.0) == 4.0
    assert _congestion_potential(3.0) == 9.0


def test_congestion_zero_for_zero_resource() -> None:
    assert _congestion(make_job(resource=0.0), 0, [5.0]) == 0.0


def test_congestion_is_marginal_increase() -> None:
    # (0 + 1)^2 - 0^2 = 1
    assert _congestion(make_job(), 0, [0.0]) == 1.0
    # (2 + 1)^2 - 2^2 = 5, (4 + 1)^2 - 4^2 = 9
    assert _congestion(make_job(), 0, [2.0]) == pytest.approx(5.0)
    assert _congestion(make_job(), 0, [4.0]) == pytest.approx(9.0)


def test_congestion_sums_over_occupied_slots() -> None:
    load = [0.0, 2.0, 4.0, 100.0]
    # slots 1 and 2 only: 5 + 9
    assert _congestion(make_job(slots=2), 1, load) == pytest.approx(14.0)


def test_carbon_cost_spreads_energy_over_occupied_slots() -> None:
    carbon = [100.0, 200.0, 300.0]
    # 2 kWh over 2 slots: 1 kWh x 200 + 1 kWh x 300
    assert _carbon_cost(make_job(slots=2, energy=2.0), 1, carbon) == pytest.approx(
        500.0
    )


def test_delay_is_minutes_from_arrival_to_slot_start() -> None:
    cfg = CoastConfig()
    assert _delay(make_job(arrival=10.0), 2, cfg) == pytest.approx(50.0)


def test_job_cost_is_weighted_normalised_sum() -> None:
    cfg = CoastConfig(
        carbon_weight=2.0,
        delay_weight=3.0,
        congestion_weight=5.0,
        carbon_ref=10.0,
        congestion_ref=4.0,
    )
    job = make_job(energy=1.0)
    carbon, load = [0.0, 100.0], [0.0, 3.0]
    # carbon 100 g / 10, delay 30 min / 60, congestion (16 - 9) / 4
    expected = 2.0 * 10.0 + 3.0 * 0.5 + 5.0 * 7.0 / 4.0
    assert _job_cost(job, 1, load, carbon, cfg) == pytest.approx(expected)


def test_all_zero_weights_give_zero_cost() -> None:
    cfg = CoastConfig(carbon_weight=0.0, delay_weight=0.0, congestion_weight=0.0)
    assert _job_cost(make_job(), 1, [5.0, 5.0], [300.0, 300.0], cfg) == 0.0


def test_best_slot_matches_brute_force() -> None:
    """The sliding-window best response equals the argmin of the cost."""
    cfg = CoastConfig(congestion_weight=0.01)
    carbon = [180.0, 120.0, 60.0, 90.0, 150.0, 40.0, 200.0, 70.0]
    load = [30.0, 10.0, 80.0, 0.0, 20.0, 60.0, 5.0, 0.0]
    for slots in (1, 2, 3):
        job = make_job(slots=slots, resource=8.0, energy=3.0)
        feasible = range(len(carbon) - slots + 1)
        costs = [_job_cost(job, t, load, carbon, cfg) for t in feasible]
        assert _best_slot(job, feasible, load, carbon, cfg) == costs.index(min(costs))


def test_best_slot_breaks_ties_to_earliest() -> None:
    cfg = CoastConfig(delay_weight=0.0, congestion_weight=0.0)
    assert _best_slot(make_job(), range(3), [0.0] * 3, [50.0] * 3, cfg) == 0
