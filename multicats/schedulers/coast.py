"""COAST: congestion-aware start-time recommendations for carbon-aware HPC jobs.

W. Feng et al., "COAST: Congestion-Aware Start-Time Recommendations for
Carbon-Aware HPC Jobs", IEEE GreenCom 2026, https://arxiv.org/abs/2609.05443.
The reference implementation and trace-driven evaluation live in
https://github.com/SMART-DRI/algorithms (``coast/``); this module ports its
batch solver and must produce identical assignments
(see ``tests/test_coast_reference.py``).

Time is discretised into slots of ``slot_minutes``. For job ``i`` occupying
``ell_i`` slots from start slot ``t``, the per-job cost is

    J_i(t) = alpha * C_i(t) / carbon_ref
           + beta  * W_i(t) / delay_ref
           + gamma * D_i(t) / congestion_ref

- ``C_i(t)``: carbon (gCO2), the job's energy (kWh) spread evenly over its
  occupied slots, weighted by each slot's carbon intensity (gCO2/kWh);
- ``W_i(t)``: delay (minutes) from arrival to the start of slot ``t``;
- ``D_i(t)``: congestion, ``sum_s psi(L_s + r_i) - psi(L_s)`` over the
  occupied slots with ``psi(x) = x**2``, where ``L`` is the load (resource
  units, e.g. CPUs) excluding job ``i``.

The ``*_ref`` constants are reference scales that make the three terms
dimensionless before weighting, so that the weights trade off comparable
quantities:

- ``carbon_ref`` (gCO2): carbon cost of the median job at the mean carbon
  intensity;
- ``delay_ref`` (default 1 h): one ``delay_ref`` of delay counts as one unit;
- ``congestion_ref`` (resource units^2 x slots): congestion the median job
  causes on its own in an empty slot, ``r**2 * ell``.

With the defaults, each term is about 1 for the median job of the CEA-Curie
trace used in the paper; :func:`calibrate` refits them for another cluster.

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

    The default weights and reference constants are those selected in the
    paper on the CEA-Curie trace (alpha=4, beta=1, gamma=0.0005); see the
    module docstring for the meaning and units of the ``*_ref`` constants.

    ``slot_minutes`` defaults to ``None``, meaning the resolution of the
    carbon-intensity forecast (30 min for the UK Carbon Intensity API, 15 min
    for Wattnet); the scheduler resolves it when it is created. An explicit
    value must be a multiple of the forecast resolution.

    Baselines from the paper are special cases: ``congestion_weight=0`` gives
    Carbon+waiting, and additionally ``delay_weight=0`` gives Carbon-greedy.
    """

    carbon_weight: float = 4.0  # alpha
    delay_weight: float = 1.0  # beta
    congestion_weight: float = 0.0005  # gamma
    carbon_ref: float = 82.6623227202825  # gCO2
    delay_ref: timedelta = field(default_factory=lambda: timedelta(hours=1))
    congestion_ref: float = 9216.0  # resource units^2 x slots
    slot_minutes: int | None = None  # None: carbon forecast resolution
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
        if self.slot_minutes is not None and self.slot_minutes <= 0:
            raise ValueError("slot_minutes must be positive")
        if self.horizon <= timedelta(0):
            raise ValueError("horizon must be positive")
        if self.slot_minutes is not None and self.horizon % self.slot_duration:
            raise ValueError("horizon must be a multiple of slot_minutes")
        if self.max_sweeps < 1:
            raise ValueError("max_sweeps must be at least 1")

    @property
    def slot_duration(self) -> timedelta:
        return timedelta(minutes=_slot_minutes(self))

    @property
    def horizon_slots(self) -> int:
        return int(self.horizon / self.slot_duration)


def _slot_minutes(cfg: CoastConfig) -> int:
    if cfg.slot_minutes is None:
        raise ValueError(
            "slot_minutes is not set; CoastScheduler takes it from the carbon "
            + "provider, or set it explicitly"
        )
    return cfg.slot_minutes


def _resolve_slot(cfg: CoastConfig, resolution: int) -> CoastConfig:
    """Return ``cfg`` with ``slot_minutes`` set for a forecast resolution (min).

    Slots shorter than the forecast interval would leave slots without carbon
    data, so an explicit slot length must be a multiple of the resolution;
    longer slots average the forecast points they contain.
    """
    slot = resolution if cfg.slot_minutes is None else cfg.slot_minutes
    if slot % resolution:
        raise ValueError(
            f"slot_minutes ({slot}) must be a multiple of the carbon forecast "
            + f"resolution ({resolution} min)"
        )
    return replace(cfg, slot_minutes=slot)


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
    slot = _slot_minutes(cfg)
    # runtime rounded up to whole minutes, with a floor of one slot
    runtime = max(slot, math.ceil(job.duration.total_seconds() / 60.0))
    return _SlotJob(
        job_id=job.job_id,
        arrival=_minutes(job.arrival - origin),
        runtime=runtime,
        slots=max(1, math.ceil(runtime / slot)),
        resource=float(job.resource),
        energy=job.energy,
        max_wait=_minutes(job.max_wait),
        deadline=_minutes(job.deadline - origin),
    )


def _earliest_slot(job: _SlotJob, decision: float, cfg: CoastConfig) -> int:
    """First slot at or after both the arrival and the decision time."""
    return math.ceil(max(job.arrival, decision) / _slot_minutes(cfg) - 1e-9)


def _feasible_range(
    job: _SlotJob, earliest: int, n_slots: int, cfg: CoastConfig
) -> range:
    """Start slots within max wait, deadline, horizon and the data arrays."""
    slot = _slot_minutes(cfg)
    latest_by_wait = math.floor((job.arrival + job.max_wait) / slot)
    latest_by_deadline = math.floor((job.deadline - job.runtime) / slot)
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
    return t * _slot_minutes(cfg) - job.arrival


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
    slot = _slot_minutes(cfg)
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
        delay_term = (t * slot - job.arrival) / delay_ref
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

    Jobs are placed greedily in the given order, each best-responding to the
    load placed so far, then swept until a full sweep moves no job (a pure
    Nash equilibrium) or ``cfg.max_sweeps`` sweeps have run. A job moves only
    if that strictly lowers its cost; ties keep its current slot.

    :param jobs: jobs of the batch, in placement order
    :param decision_time: when the recommendation is made; no job starts before
        the first slot at or after both its arrival and this time
    :param origin: start of slot 0 of the ``carbon`` and ``background`` arrays
    :param carbon: carbon intensity (gCO2/kWh) of the slot starting at
        ``origin + k * slot_duration``, for k = 0, 1, ...
    :param background: load (resource units) already committed in the same
        slots, e.g. by jobs placed in earlier batches
    :param cfg: weights, reference constants, slot length and horizon;
        ``cfg.slot_minutes`` must be set
    :return: ``({job_id: slot index}, sweeps)``. Slot index ``k`` means a start
        at ``origin + k * slot_duration``; jobs without a feasible slot within
        the arrays are omitted. ``sweeps`` counts best-response sweeps,
        including the final one in which no job moved.
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
    """Return ``cfg`` with ``carbon_ref`` and ``congestion_ref`` fitted to a job
    sample, so that each term is about 1 for the median job.

    ``carbon_ref`` is the median of energy x ``mean_carbon`` and
    ``congestion_ref`` the median of ``resource**2 x occupied slots`` (a job's
    congestion in an empty slot). The weights are not changed.

    :param cfg: configuration to update; ``cfg.slot_minutes`` must be set
    :param jobs: a representative sample of the cluster's jobs, e.g. from its
        accounting history
    :param mean_carbon: mean carbon intensity (gCO2/kWh) of the cluster's grid
        region. It sets the scale of the carbon term: a lower value makes
        carbon count for more relative to delay and congestion. The paper uses
        the mean over its whole one-year GB trace; for a deployment, use the
        mean of the past year of historical intensity for the region, so that
        seasonal variation averages out.
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
    at or after the current slot.

    Forecast points falling in the same slot are averaged; points before the
    current slot are ignored, and the series stops at the first slot without
    data.
    """
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
        carbon: BaseProvider,
        config: CoastConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        :param carbon: carbon-intensity forecast provider from ``cats.providers``
        :param config: scheduler configuration; ``slot_minutes`` is resolved
            against the provider's forecast resolution
        :param clock: returns the current time; defaults to the UTC wall clock
        """
        if not isinstance(carbon, BaseProvider):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("CoastScheduler needs a carbon-intensity provider")  # pyright: ignore[reportUnreachable]
        self.carbon: BaseProvider = carbon
        self.config: CoastConfig = _resolve_slot(
            config or CoastConfig(), carbon.get_temporal_resolution_minutes()
        )
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    @override
    def assign(
        self,
        jobs: list[JobParams],
        background_load: float,
    ) -> dict[str, datetime | None]:
        """Recommend a start time for every job in the batch.

        ``background_load`` is a single cluster-wide value. A load that is the
        same in every slot adds the same congestion to every candidate start,
        so it cannot change COAST's decisions and is not used; congestion is
        computed among the jobs of this batch.

        Jobs with no feasible slot inside the forecast (for example, jobs
        longer than the forecast horizon) are rejected and map to ``None``.
        """
        if not jobs:
            return {}
        now = self.clock()
        origin, carbon = _carbon_slots(self.carbon, now, self.config)
        background = [0.0] * len(carbon)
        ordered = sorted(jobs, key=lambda job: job.arrival)
        slots, _ = solve_batch(ordered, now, origin, carbon, background, self.config)
        return {
            job.job_id: (
                origin + slots[job.job_id] * self.config.slot_duration
                if job.job_id in slots
                else None
            )
            for job in jobs
        }
