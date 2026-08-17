import pytest

from multicats.schedulers.coast import (
    CoastConfig,
    _congestion,
    _congestion_potential,
    _job_cost,
)


def test_congestion_potential_is_quadratic() -> None:
    assert _congestion_potential(0.0) == 0.0
    assert _congestion_potential(2.0) == 4.0
    assert _congestion_potential(3.0) == 9.0


def test_congestion_zero_when_no_load() -> None:
    assert _congestion(job_resource=0.0, background_load=0.0, other_resource=0.0) == 0.0


def test_congestion_marginal_increase() -> None:
    # phi(0 + 0 + 1) - phi(0 + 0) = 1 - 0 = 1
    assert _congestion(job_resource=1.0, background_load=0.0, other_resource=0.0) == 1.0


def test_congestion_increases_with_base_load() -> None:
    # phi(2 + 1) - phi(2) = 9 - 4 = 5
    c1 = _congestion(job_resource=1.0, background_load=2.0, other_resource=0.0)
    assert c1 == pytest.approx(5.0)

    # phi(4 + 1) - phi(4) = 25 - 16 = 9
    c2 = _congestion(job_resource=1.0, background_load=4.0, other_resource=0.0)
    assert c2 == pytest.approx(9.0)

    assert c2 > c1


def test_congestion_background_and_other_are_symmetric() -> None:
    c_bg = _congestion(job_resource=1.0, background_load=3.0, other_resource=0.0)
    c_other = _congestion(job_resource=1.0, background_load=0.0, other_resource=3.0)
    assert c_bg == pytest.approx(c_other)


def test_job_cost_carbon_only() -> None:
    cfg = CoastConfig(carbon_weight=2.0, delay_weight=0.0, congestion_weight=0.0)
    cost = _job_cost(
        carbon_intensity=100.0,
        energy=0.5,
        delay=0.0,
        job_resource=1.0,
        background_load=0.0,
        other_resource=0.0,
        cfg=cfg,
    )
    assert cost == pytest.approx(2.0 * 100.0 * 0.5)


def test_job_cost_delay_only() -> None:
    cfg = CoastConfig(carbon_weight=0.0, delay_weight=3.0, congestion_weight=0.0)
    cost = _job_cost(
        carbon_intensity=0.0,
        energy=0.0,
        delay=120.0,
        job_resource=0.0,
        background_load=0.0,
        other_resource=0.0,
        cfg=cfg,
    )
    assert cost == pytest.approx(3.0 * 120.0)


def test_job_cost_all_zero_weights() -> None:
    cfg = CoastConfig(carbon_weight=0.0, delay_weight=0.0, congestion_weight=0.0)
    cost = _job_cost(
        carbon_intensity=500.0,
        energy=10.0,
        delay=3600.0,
        job_resource=4.0,
        background_load=2.0,
        other_resource=1.0,
        cfg=cfg,
    )
    assert cost == pytest.approx(0.0)


def test_job_cost_additive() -> None:
    cfg = CoastConfig(carbon_weight=1.0, delay_weight=1.0, congestion_weight=1.0)
    carbon = 100.0 * 0.5
    delay = 60.0
    cong = _congestion(1.0, 0.0, 0.0)
    expected = carbon + delay + cong
    cost = _job_cost(
        carbon_intensity=100.0,
        energy=0.5,
        delay=60.0,
        job_resource=1.0,
        background_load=0.0,
        other_resource=0.0,
        cfg=cfg,
    )
    assert cost == pytest.approx(expected)
