# MultiCATS Feature List

## Core Data Model

- **JobParams** struct: arrival slot `a`, resource demand `r`, runtime `d`, estimated energy `e_hat`, deadline `D`, max wait time `m`
- Job ownership model: user supplies `a` and `d`; cluster operator sets `D`, `m`, and scheduler weights; Slurm queue provides background load `B` and energy estimates `e_hat`

## Interfaces

### QueueProvider (abstract)
Reads background load and per-job energy estimates from the cluster queue.

- `slurm_cli` — implementation via `squeue --json` / `sinfo --json` / `sacct --json`

### Scheduler (abstract) — `multicats/schedulers/`
Given a set of pending jobs and cluster state, assigns each job a start slot.
Each scheduler is self-contained: it defines its own configuration and lives in `multicats/schedulers/`.

- `fifo` — first-in-first-out baseline (arrival order, no carbon awareness)
- `random` — random slot assignment baseline
- `coast` — Carbon-Optimised Adaptive Scheduling (game-theoretic, see below)

### Controller (abstract)
Submits or holds jobs in the cluster scheduler.

- `slurm` — holds/releases jobs via Slurm commands

## StaticConfig

Single configuration object selecting one QueueProvider, one Scheduler, and one Controller at startup.
Scheduler-specific parameters (e.g. COAST weights) are defined by the scheduler itself, not here.

| Parameter        | Description                                      |
|------------------|--------------------------------------------------|
| `microbatch_size`| Periodicity of pulling new jobs from Slurm queue |

## COAST Scheduler (`multicats/schedulers/coast.py`)

COAST-specific config (set by cluster operator alongside StaticConfig):

| Parameter          | Description                                        |
|--------------------|----------------------------------------------------|
| `carbon_weight`    | Weight on carbon cost `(carbon_intensity * e_hat)` |
| `delay_weight`     | Weight on delay                                    |
| `congestion_weight`| Weight on congestion                               |
| `slot_duration`    | Slot size (timedelta) for time discretisation      |

Cost function per job `i`:

```
cost_i = carbon_weight    * (carbon_intensity(slot) * e_hat_i)
       + delay_weight     * delay_i
       + congestion_weight * congestion_i
```

Congestion term:

```
congestion_i = phi(B + sum_{j!=i} r_j + r_i) - phi(B + sum_{j!=i} r_j)
phi(x) = x^2
```

- Iterative game-theoretic update: each job best-responds in turn until convergence
- Background load `B` sourced from QueueProvider at each microbatch tick

## Carbon Intensity Integration

- Fetch real-time carbon intensity for the cluster's grid region
- Fetch forecast carbon intensity over the scheduling horizon (slots up to max deadline)
- Pluggable data source (e.g. Electricity Maps, National Grid ESO)

## Evaluation Framework

- Simulate or replay job traces against all three strategies (fifo, coast, random)
- Metrics: total carbon cost, mean job delay, peak congestion, makespan
- Per-job breakdown and aggregate statistics
- Document required Slurm logging configuration for trace collection
