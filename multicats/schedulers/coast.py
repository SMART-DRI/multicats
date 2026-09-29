"""COAST: congestion-aware start-time recommendations for carbon-aware HPC jobs.

W. Feng et al., "COAST: Congestion-Aware Start-Time Recommendations for
Carbon-Aware HPC Jobs", IEEE GreenCom 2026, https://arxiv.org/abs/2609.05443.
The reference implementation and trace-driven evaluation live in
https://github.com/SMART-DRI/algorithms (``coast/``); this module ports its
batch solver and must produce identical assignments
(see ``tests/test_coast_reference.py``).

Time is discretised into slots of length ``slot_duration``. For job ``i``
occupying ``ell_i`` slots from start slot ``t``, the per-job cost is

    J_i(t) = alpha * C_i(t) / carbon_ref
           + beta  * W_i(t) / delay_ref
           + gamma * D_i(t) / congestion_ref

- ``C_i(t)``: carbon (gCO2), the job's energy spread evenly over its occupied
  slots, weighted by each slot's carbon intensity;
- ``W_i(t)``: delay (minutes) from arrival to the start of slot ``t``;
- ``D_i(t)``: congestion, ``sum_s psi(L_s + r_i) - psi(L_s)`` over the
  occupied slots with ``psi(x) = x**2``, where ``L`` is the load (resource
  units, e.g. CPUs) excluding job ``i``.

With a quadratic ``psi`` each batch is an exact potential game, so sequential
strict best response terminates at a pure Nash equilibrium.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import accumulate
from statistics import median

from cats.providers.base import BaseProvider
from typing_extensions import override

from ..interfaces import Scheduler
from ..models import JobParams

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class CoastConfig:
    """Operator configuration for the COAST scheduler.

    The defaults are the weights and normalisation constants selected in the
    paper on the CEA-Curie trace (alpha=4, beta=1, gamma=0.0005). The
    normalisation constants depend on the cluster's job mix and should be
    recalibrated with :func:`calibrate` for other clusters.

    Baselines from the paper are special cases: ``congestion_weight=0`` gives
    Carbon+waiting, and additionally ``delay_weight=0`` gives Carbon-greedy.
    """

    carbon_weight: float = 4.0  # alpha
    delay_weight: float = 1.0  # beta
    congestion_weight: float = 0.0005  # gamma
    carbon_ref: float = 82.6623227202825  # gCO2
    delay_ref: timedelta = field(default_factory=lambda: timedelta(hours=1))
    congestion_ref: float = 9216.0  # resource^2 * slots
    slot_duration: timedelta = field(default_factory=lambda: timedelta(minutes=30))
    horizon: timedelta = field(default_factory=lambda: timedelta(hours=48))
    max_sweeps: int = 50
    location: str | None = None  # passed to the carbon-intensity provider

    def __post_init__(self) -> None:
        if min(self.carbon_weight, self.delay_weight, self.congestion_weight) < 0:
            raise ValueError("COAST weights must be non-negative")
        if self.carbon_ref <= 0 or self.congestion_ref <= 0:
            raise ValueError("normalisation constants must be positive")
        if self.delay_ref <= timedelta(0):
            raise ValueError("delay_ref must be positive")
        if self.slot_duration <= timedelta(0) or self.slot_duration % timedelta(
            minutes=1
        ):
            raise ValueError("slot_duration must be a positive whole number of minutes")
        if self.horizon < self.slot_duration or self.horizon % self.slot_duration:
            raise ValueError("horizon must be a positive multiple of slot_duration")
        if self.max_sweeps < 1:
            raise ValueError("max_sweeps must be at least 1")

    @property
    def slot_minutes(self) -> int:
        return int(self.slot_duration / timedelta(minutes=1))

    @property
    def horizon_slots(self) -> int:
        return int(self.horizon / self.slot_duration)


@dataclass(frozen=True)
class _SlotJob:
    """A job expressed in minutes relative to the slot-grid origin."""

    job_id: str
    arrival: float  # minutes
    runtime: int  # minutes, at least one slot
    slots: int  # ell
    resource: float  # r
    energy: float  # kWh
    max_wait: float  # minutes
    deadline: float  # minutes


def _minutes(delta: timedelta) -> float:
    return delta.total_seconds() / 60.0


def slot_origin(now: datetime, slot_duration: timedelta) -> datetime:
    """Start of the slot containing ``now``. Slots are aligned to the Unix
    epoch, so 30-minute slots coincide with UTC half-hours."""
    return now - (now - _EPOCH) % slot_duration


def _to_slot_job(job: JobParams, origin: datetime, cfg: CoastConfig) -> _SlotJob:
    # runtime rounded up to whole minutes, with a floor of one slot
    runtime = max(cfg.slot_minutes, math.ceil(job.duration.total_seconds() / 60.0))
    return _SlotJob(
        job_id=job.job_id,
        arrival=_minutes(job.arrival - origin),
        runtime=runtime,
        slots=max(1, math.ceil(runtime / cfg.slot_minutes)),
        resource=float(job.resource),
        energy=job.energy,
        max_wait=_minutes(job.max_wait),
        deadline=_minutes(job.deadline - origin),
    )


def _earliest_slot(job: _SlotJob, decision: float, cfg: CoastConfig) -> int:
    """First slot at or after both the arrival and the decision time."""
    return math.ceil(max(job.arrival, decision) / cfg.slot_minutes - 1e-9)


def _feasible_range(
    job: _SlotJob, earliest: int, n_slots: int, cfg: CoastConfig
) -> range:
    """Start slots within max wait, deadline, horizon and the data arrays."""
    latest_by_wait = math.floor((job.arrival + job.max_wait) / cfg.slot_minutes)
    latest_by_deadline = math.floor((job.deadline - job.runtime) / cfg.slot_minutes)
    latest_by_horizon = earliest + cfg.horizon_slots - 1
    latest_by_data = n_slots - job.slots
    latest = min(latest_by_wait, latest_by_deadline, latest_by_horizon, latest_by_data)
    return range(earliest, latest + 1)


def _congestion_potential(load: float) -> float:
    return load * load


def _carbon_cost(job: _SlotJob, t: int, carbon: Sequence[float]) -> float:
    """C_i(t) in gCO2: energy spread uniformly over the occupied slots."""
    return job.energy / job.slots * sum(carbon[t : t + job.slots])


def _delay(job: _SlotJob, t: int, cfg: CoastConfig) -> float:
    """W_i(t) in minutes."""
    return t * cfg.slot_minutes - job.arrival


def _congestion(job: _SlotJob, t: int, load_excl: Sequence[float]) -> float:
    """D_i(t): increase of the congestion potential over the occupied slots,
    given the load excluding job i."""
    r = job.resource
    return sum(
        _congestion_potential(load + r) - _congestion_potential(load)
        for load in load_excl[t : t + job.slots]
    )


def _job_cost(
    job: _SlotJob,
    t: int,
    load_excl: Sequence[float],
    carbon: Sequence[float],
    cfg: CoastConfig,
) -> float:
    """J_i(t) with normalisation, given the load excluding job i."""
    return (
        cfg.carbon_weight * (_carbon_cost(job, t, carbon) / cfg.carbon_ref)
        + cfg.delay_weight * (_delay(job, t, cfg) / _minutes(cfg.delay_ref))
        + cfg.congestion_weight * (_congestion(job, t, load_excl) / cfg.congestion_ref)
    )


def _best_slot(
    job: _SlotJob,
    feasible: range,
    load_excl: Sequence[float],
    carbon: Sequence[float],
    cfg: CoastConfig,
) -> int:
    """Best response over ``feasible`` (ties go to the earliest slot).

    Uses ``D_i(t) = 2 r * sum_s L_s + r**2 * ell`` (from ``psi(x) = x**2``) so
    that both the carbon and congestion terms are sliding-window sums.
    """
    ell, r = job.slots, job.resource
    lo, hi = feasible.start, feasible.stop - 1
    load_cum = list(accumulate(load_excl[lo : hi + ell], initial=0.0))
    carbon_cum = list(accumulate(carbon[lo : hi + ell], initial=0.0))
    delay_ref = _minutes(cfg.delay_ref)
    best_t, best_cost = lo, math.inf
    for t in feasible:
        k = t - lo
        carbon_sum = carbon_cum[k + ell] - carbon_cum[k]
        load_sum = load_cum[k + ell] - load_cum[k]
        carbon_term = job.energy / ell * carbon_sum / cfg.carbon_ref
        delay_term = (t * cfg.slot_minutes - job.arrival) / delay_ref
        congestion_term = (2.0 * r * load_sum + r * r * ell) / cfg.congestion_ref
        cost = (
            cfg.carbon_weight * carbon_term
            + cfg.delay_weight * delay_term
            + cfg.congestion_weight * congestion_term
        )
        if cost < best_cost:
            best_t, best_cost = t, cost
    return best_t


def _add_load(load: list[float], job: _SlotJob, t: int) -> None:
    for s in range(t, t + job.slots):
        load[s] += job.resource


def _remove_load(load: list[float], job: _SlotJob, t: int) -> None:
    for s in range(t, t + job.slots):
        load[s] -= job.resource


def solve_batch(
    jobs: Sequence[JobParams],
    decision_time: datetime,
    origin: datetime,
    carbon: Sequence[float],
    background: Sequence[float],
    cfg: CoastConfig,
) -> tuple[dict[str, int], int]:
    """Assign start slots to one micro-batch by strict best response.

    ``carbon[k]`` (gCO2/kWh) and ``background[k]`` (resource units already
    committed) describe the slot starting at ``origin + k * slot_duration``.
    Jobs are placed greedily in the given order, then swept until a full sweep
    moves no job (a pure Nash equilibrium) or ``max_sweeps`` is reached.

    Returns ``({job_id: slot index}, sweeps)``; jobs without a feasible slot
    are omitted.
    """
    n_slots = min(len(carbon), len(background))
    decision = _minutes(decision_time - origin)
    load = [float(x) for x in background[:n_slots]]
    active: list[tuple[_SlotJob, range]] = []
    assign: dict[str, int] = {}

    # initial placement: best response to the load placed so far
    for params in jobs:
        job = _to_slot_job(params, origin, cfg)
        feasible = _feasible_range(
            job, _earliest_slot(job, decision, cfg), n_slots, cfg
        )
        if not feasible:
            continue
        t = _best_slot(job, feasible, load, carbon, cfg)
        assign[job.job_id] = t
        _add_load(load, job, t)
        active.append((job, feasible))

    # strict best-response sweeps
    sweeps = 0
    for _ in range(cfg.max_sweeps):
        sweeps += 1
        moved = False
        for job, feasible in active:
            current = assign[job.job_id]
            _remove_load(load, job, current)  # load now excludes the job
            t = _best_slot(job, feasible, load, carbon, cfg)
            if t != current:
                new_cost = _job_cost(job, t, load, carbon, cfg)
                current_cost = _job_cost(job, current, load, carbon, cfg)
                if new_cost < current_cost - 1e-12:
                    assign[job.job_id] = t
                    moved = True
            _add_load(load, job, assign[job.job_id])
        if not moved:
            break
    return assign, sweeps


def calibrate(
    cfg: CoastConfig, jobs: Sequence[JobParams], mean_carbon: float
) -> CoastConfig:
    """Return ``cfg`` with normalisation constants fitted to a job sample.

    ``carbon_ref`` is the median of energy x ``mean_carbon`` (gCO2/kWh) and
    ``congestion_ref`` the median of ``resource**2 x occupied slots`` (a job's
    congestion in an empty slot), so each term is about 1 for the median job.
    """
    if not jobs:
        raise ValueError("calibration needs at least one job")
    origin = min(job.arrival for job in jobs)
    slot_jobs = [_to_slot_job(job, origin, cfg) for job in jobs]
    carbon_ref = median(job.energy * mean_carbon for job in slot_jobs)
    congestion_ref = median(job.resource**2 * job.slots for job in slot_jobs)
    return replace(
        cfg,
        carbon_ref=carbon_ref or 1.0,
        congestion_ref=congestion_ref or 1.0,
    )


def _carbon_slots(
    provider: BaseProvider, now: datetime, cfg: CoastConfig
) -> tuple[datetime, list[float]]:
    """Carbon intensity per slot, starting at the first slot with forecast data
    at or after the current slot. Forecast points falling in the same slot are
    averaged; the series stops at the first slot without data."""
    series = provider.get_data(now, location=cfg.location)
    origin = slot_origin(now, cfg.slot_duration)
    by_slot: dict[int, list[float]] = {}
    for point in series.values:
        k = (point.datetime - origin) // cfg.slot_duration
        if k >= 0:
            by_slot.setdefault(k, []).append(float(point.value))
    if not by_slot:
        raise ValueError("carbon-intensity forecast has no data from now onwards")
    first = min(by_slot)
    values: list[float] = []
    k = first
    while k in by_slot:
        values.append(sum(by_slot[k]) / len(by_slot[k]))
        k += 1
    return origin + first * cfg.slot_duration, values


class CoastScheduler(Scheduler):
    """Congestion-aware start-time recommendations (COAST)."""

    def __init__(
        self,
        config: CoastConfig | None = None,
        carbon: BaseProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config: CoastConfig = config or CoastConfig()
        self.carbon: BaseProvider | None = carbon
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    @override
    def assign(
        self,
        jobs: list[JobParams],
        background_load: float,
    ) -> dict[str, datetime]:
        """Recommend a start time for every job in the batch.

        ``background_load`` is a single cluster-wide value. A load that is the
        same in every slot adds the same congestion to every candidate start,
        so it cannot change COAST's decisions and is not used; congestion is
        computed among the jobs of this batch.

        Jobs with no feasible slot inside the forecast (for example, jobs
        longer than the forecast horizon) are recommended to start now.
        """
        if not jobs:
            return {}
        if self.carbon is None:
            raise ValueError("CoastScheduler needs a carbon-intensity provider")
        now = self.clock()
        origin, carbon = _carbon_slots(self.carbon, now, self.config)
        background = [0.0] * len(carbon)
        ordered = sorted(jobs, key=lambda job: job.arrival)
        slots, _ = solve_batch(ordered, now, origin, carbon, background, self.config)
        return {
            job.job_id: (
                origin + slots[job.job_id] * self.config.slot_duration
                if job.job_id in slots
                else max(job.arrival, now)
            )
            for job in jobs
        }
