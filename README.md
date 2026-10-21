# MultiCATS

[![CI](https://github.com/SMART-DRI/multicats/actions/workflows/ci.yml/badge.svg)](https://github.com/SMART-DRI/multicats/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![Status: early development](https://img.shields.io/badge/status-early%20development-orange.svg)](docs/features/plan.md)

Multiple-job optimisation for carbon intensity and green scheduling.

MultiCATS extends [CATS](https://cats.readthedocs.io) — which shifts a
*single* job to a low-carbon time — to a whole Slurm queue. Instead of each job
independently picking the greenest slot (and all of them piling into it),
MultiCATS assigns start times across the pending queue jointly, trading carbon
intensity off against user-visible delay and cluster congestion.

> **Status: early development.** The data model, provider, controller, the
> random baseline and the COAST scheduler are implemented. There is no
> long-running daemon yet: the `multicats schedule` command runs one microbatch,
> and [DEPLOYMENT.md](DEPLOYMENT.md) shows how to run it on a schedule. See
> [docs/features/plan.md](docs/features/plan.md) for what is done and what is next.

## How it works

This is a brief description of the COAST scheduling algorithm. For details see
W. Feng et al., [COAST: Congestion-Aware Start-Time Recommendations for
Carbon-Aware HPC Jobs](https://arxiv.org/abs/2609.05443) (IEEE GreenCom 2026)
and the module docstring in
[multicats/schedulers/coast.py](multicats/schedulers/coast.py). MultiCATS is written to be easily extendable to support multiple scheduling algorithms.

Each pending job is described by `(a, r, d, e_hat, D, m)` — arrival, resource
demand, runtime, estimated energy, deadline, and maximum acceptable wait — and
is assigned a start time minimising

```
cost_i = alpha * (carbon_intensity(slot) * e_hat_i)   # carbon
       + beta  * delay_i                              # user-visible delay
       + gamma * congestion_i                         # contention with other jobs
```

where congestion is the marginal load a job adds on top of everything else
already scheduled in that slot:

```
congestion_i = phi(B + sum_{j != i} r_j + r_i) - phi(B + sum_{j != i} r_j)
phi(x) = x^2
```

`B` is the cluster's background load, read from Slurm each microbatch tick. The
quadratic potential is what stops every job from stacking into the same
low-carbon window: the second job into a slot pays more than the first. COAST
resolves the resulting assignment by iterated best response until it converges.

Who owns which parameter:

| Slurm queue | User | Cluster operator |
|---|---|---|
| background load `B` | arrival slot `a` | max wait `m`, deadline `D` |
| energy estimate `e_hat` | runtime `d` | weights `alpha`, `beta`, `gamma`, slot size, microbatch period |

Carbon intensity is obtained from the providers in [CATS](https://cats.readthedocs.io) (`cats.providers`).

## Architecture

Three pluggable interfaces ([multicats/interfaces.py](multicats/interfaces.py)),
each selected by name in `StaticConfig`:

- **`QueueProvider`** — reads cluster state. `slurm_cli` shells out to
  `squeue --json`, `sinfo --json` and `sacct --json`; energy comes from
  `ConsumedEnergyRaw` where accounting provides it, otherwise from a
  CPU-wattage heuristic.
- **`Scheduler`** — maps pending jobs plus background load to `{job_id: start}`.
  Each scheduler owns its own config dataclass and its command-line options
  (`add_arguments()` / `from_args()`). `random` is implemented as a baseline;
  `coast` is the real one; `fifo` is planned. `multicats schedule --scheduler`
  picks one (default `coast`) from the `SCHEDULERS` registry in
  [multicats/schedulers/\_\_init\_\_.py](multicats/schedulers/__init__.py).
- **`Controller`** — applies the assignment. `slurm` holds and releases jobs via
  `scontrol` / `scancel`, clamping start times to at least `min_lead_time` in
  the future because Slurm silently discards start times in the past.

The `multicats` command line is extensible the same way: each subcommand
(currently `schedule`) is a `Command` registered in `COMMANDS` in
[multicats/cli/\_\_init\_\_.py](multicats/cli/__init__.py). To add one, write
a module in `multicats/cli/` that defines a `Command` and register it there.

File structure:
```
multicats/
  models.py       JobParams, feasibility window (earliest/latest start)
  interfaces.py   QueueProvider / Scheduler / Controller ABCs
  config.py       StaticConfig — picks one of each, plus microbatch_size
  providers/      slurm_cli.py
  schedulers/     coast.py, random.py
  controllers/    slurm.py
  cli/            the multicats command; schedule.py runs one microbatch
```

Jobs are gated at submission by a Lua job-submit plugin
([cluster/job_submit.lua](cluster/job_submit.lua)): anything landing in the
`multicats` partition gets a begin time a year out, so it cannot start until
MultiCATS moves it.

## Deploying on a cluster

[DEPLOYMENT.md](DEPLOYMENT.md) covers installing MultiCATS on a Slurm cluster:
creating the partition, installing the job-submit plugin, running COAST every
few minutes with a systemd timer or cron, and monitoring what it does. The
command it runs is:

```bash
multicats schedule --location OX1 --dry-run   # drop --dry-run to apply start times
multicats schedule --scheduler random --dry-run   # baseline, no carbon forecast
```

## Install

Requires Python 3.11+. Using [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
```

## Tests

Unit tests need nothing but Python — e2e tests are excluded by default via the
`addopts` in [pyproject.toml](pyproject.toml):

```bash
uv run pytest
```

The e2e tests need a live Slurm cluster. A disposable one runs in Docker, as
the Compose project `multicats`. Its containers have fixed names (`mysql`,
`slurmdbd`, `slurmctld`, `c1`, `c2`), so stop any other Slurm Docker cluster
using those names first:

```bash
./cluster/test_e2e.sh      # start cluster, install plugin + package, run e2e, tear down
```

To keep the cluster up between runs instead:

```bash
./cluster/start.sh
./cluster/install_multicats.sh
./cluster/run_tests.sh
./cluster/stop.sh
```

The `cluster/debug_*.sh` scripts probe Slurm's `scontrol update StartTime`
behaviour directly — useful when the controller's assignments do not take.

## Docs

- [DEPLOYMENT.md](DEPLOYMENT.md) — installing and running MultiCATS on a Slurm cluster
- [docs/features/features.md](docs/features/features.md) — feature list and cost-function detail
- [docs/features/plan.md](docs/features/plan.md) — phased implementation plan and open questions
- [docs/architecture_source.md](docs/architecture_source.md) — original design notes

## Open questions

- How is `e_hat` best obtained in practice — job history averages, or a cluster agent?
- Should `m` be user-specified, or operator-enforced with a cap? Users have an
  incentive to set it low.
- What heuristics should an operator use to pick `alpha`, `beta`, `gamma`?

## AI policy

multicats has been authored with the assistance of AI tooling, particularly Claude Code and Codex.

Contributions should follow guidelines established by the [CATS project](https://cats.readthedocs.io/en/latest/devguide.html).
