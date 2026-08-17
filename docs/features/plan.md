# MultiCATS Implementation Plan

## Phase 1 — Core data model and interfaces

- Define `JobParams` dataclass/struct
- Define abstract interfaces: `QueueProvider`, `Strategy`, `Controller`
- Define `StaticConfig` schema (provider/strategy/controller selection + operator params)
- Unit tests for cost function and congestion potential

## Phase 2 — Queue provider ✓

- Implement `slurm_cli` QueueProvider (`squeue --json`, `sinfo --json`, `sacct --json`)
- E2e tests against Slurm Docker cluster

## Phase 3 — Baseline schedulers (`multicats/schedulers/`)

- Implement `fifo` Scheduler
- Implement `random` Scheduler
- Each carries its own config (even if trivial); verify assignments satisfy deadline and max-wait constraints

## Phase 4 — COAST scheduler (`multicats/schedulers/coast.py`)

- Define `CoastConfig` dataclass (`carbon_weight`, `delay_weight`, `congestion_weight`, `slot_duration`) owned by the scheduler
- Implement slot-discretised carbon intensity lookup
- Implement iterative best-response update loop
- Convergence check (change in assignments below threshold between rounds)

## Phase 5 — Slurm controller

- Implement `slurm` Controller: release at assigned slot
- Handle job cancellation and re-optimisation on new arrivals (microbatch tick)

## Phase 6 — Carbon intensity integration

- Define `CarbonSource` interface: `current()` and `forecast(horizon_slots)`
- Implement at least one concrete source (e.g. Electricity Maps API)
- Cache forecast for the scheduling horizon; refresh each microbatch tick

## Phase 7 — Configuration and operator tooling

- CLI or config-file entrypoint to launch MultiCATS with a given StaticConfig
- Validate that alpha/beta/gamma are non-negative and sum to a sensible range
- Document heuristics for setting alpha, beta, gamma based on cluster priorities

## Phase 8 — Evaluation

- Collect or synthesise job traces (document required Slurm accounting config)
- Replay traces through fifo / random / coast and record metrics
- Report: carbon saved vs fifo, delay distribution, congestion over time

## Open questions to resolve

- How is `e_hat` (energy estimate) obtained in practice — averaged from job history or from a cluster agent?
- Should `m` (max wait) be user-specified or operator-enforced with a cap?
- What is the right convergence criterion for the game-theoretic update?
- Which carbon intensity data source to use and how to handle API outages?
