# Deploying MultiCATS on a Slurm cluster

This guide is for cluster operators. It sets up MultiCATS so that jobs
submitted to a `multicats` partition are held at submission, and a periodic
task assigns each one a start time. The scheduling algorithm can be chosen;
the default, COAST, picks low-carbon start times while spreading jobs out so
they don't all pile into the same slot.

## How it fits together

1. A user submits a job with `sbatch --partition=multicats ...`.
2. The `job_submit.lua` plugin sets the job's begin time (the earliest time it
   may start, shown as `EligibleTime`) a year in the future, so Slurm keeps
   it pending with `Reason=BeginTime`.
3. Every few minutes, `multicats schedule`:
   - finds jobs in the partition that are still held;
   - asks the chosen scheduler for start times. COAST, the default, fetches a
     carbon-intensity forecast through [CATS](https://cats.readthedocs.io)
     for this;
   - applies them with `scontrol update JobId=<id> StartTime=<time>`. Despite
     its name, this sets the job's begin time.
4. Slurm starts each job at, or after, its assigned time.

Jobs that already have a start time are left alone on later runs, so each job
is scheduled exactly once.

## Requirements

- **Slurm built with Lua support** for job-submit plugins. Check that
  `job_submit_lua.so` exists in your Slurm plugin directory (for example
  `/usr/lib64/slurm/`). This setup has been tested with Slurm 21.08.
- **Python 3.11 or newer** on the host that runs `multicats schedule`,
  usually the slurmctld host. It needs the Slurm client commands (`squeue`,
  `sinfo`, `sacct`, `scontrol`) on its `PATH`.
- **Operator privileges for the task.** It changes start times on other
  users' jobs, so it must run as root, as `SlurmUser`, or as an account set as
  an operator with `sacctmgr`.
- **Slurm times in UTC.** `SlurmController` sends start times to `scontrol` as
  UTC with no time zone, and reads them back the same way. If slurmctld runs in
  another time zone, every start time will be off by that offset. Run
  slurmctld with `TZ=UTC`, or don't deploy until this is fixed.
- **Outbound HTTPS** from the host to the carbon-intensity API, for the
  default COAST scheduler. The `random` baseline needs no network access.

## 1. Create the partition

Add a partition to `slurm.conf` that covers the nodes MultiCATS may schedule
onto:

```
PartitionName=multicats Nodes=ALL Default=NO MaxTime=2-00:00:00 State=UP
```

Keep `MaxTime` at or below COAST's forecast horizon (48 h by default). A job
longer than the forecast has no feasible start time, and stays held until an
operator releases it.

## 2. Install the job-submit plugin

Save this as `job_submit.lua` in the same directory as `slurm.conf`, usually
`/etc/slurm/`. It is the same file as
[cluster/job_submit.lua](cluster/job_submit.lua):

```lua
-- /etc/slurm/job_submit.lua
-- Hold all jobs submitted to the "multicats" partition until
-- MultiCATS sets the begin time via scontrol update StartTime.

local FAR_FUTURE = 365 * 24 * 60 * 60  -- 1 year

function slurm_job_submit(job_desc, part_list, submit_uid)
    if job_desc.partition == "multicats" then
        if job_desc.begin_time == 0 or job_desc.begin_time == slurm.NO_VAL then
            job_desc.begin_time = os.time() + FAR_FUTURE
            slurm.log_info("multicats: holding job in partition %s", job_desc.partition)
        end
    end
    return slurm.SUCCESS
end

function slurm_job_modify(job_desc, job_rec, part_list, modify_uid)
    return slurm.SUCCESS
end

return slurm.SUCCESS
```

Then enable it in `slurm.conf`. If `JobSubmitPlugins` is already set, add
`lua` to the comma-separated list rather than adding a second line:

```
JobSubmitPlugins=lua
```

Copy `slurm.conf` and `job_submit.lua` to every host that reads Slurm's
configuration, then restart slurmctld so it loads the plugin:

```bash
sudo systemctl restart slurmctld
```

After that, Slurm reloads `job_submit.lua` automatically when the file
changes.

The plugin only holds a job when no begin time was given. A job submitted with
`sbatch --begin=...` keeps the user's begin time and is never scheduled by
MultiCATS.

Check that the plugin holds jobs:

```bash
sbatch --partition=multicats --time=0:10:00 --wrap "hostname"
squeue --partition=multicats --Format="JobID:10,UserName:10,StateCompact:4,EligibleTime:21,Reason:12"
# ELIGIBLE_TIME about a year from now, REASON BeginTime
```

## 3. Install MultiCATS

Install into its own virtual environment on the slurmctld host:

```bash
sudo python3 -m venv /opt/multicats
sudo /opt/multicats/bin/pip install "git+https://github.com/SMART-DRI/multicats.git"
```

This installs the `multicats` command as `/opt/multicats/bin/multicats`. It
has subcommands, listed by `multicats --help`; this guide uses `multicats
schedule`, which runs one microbatch (source:
[multicats/cli/schedule.py](multicats/cli/schedule.py)).

It also installs [CATS](https://cats.readthedocs.io), which provides the
carbon-intensity forecasts, along with its own `cats` command.

## 4. Choose a carbon-intensity provider

COAST gets its forecast from a CATS provider, chosen with `--api`, for the
grid region given with `--location`. The default is
`carbonintensity.org.uk` (Great Britain); this guide's examples use it with
`--location OX1`. If your cluster is elsewhere, pick a provider and location
here and use them in place of the defaults in every command that follows.

To see the providers your installed CATS has:

```bash
/opt/multicats/bin/cats --list-providers
```

MultiCATS installs CATS from its git `main` branch, so this list can change
between installs. When this guide was written (October 2026) it was:

| `--api` | Coverage | `--location` | Resolution, horizon | Credentials | Use with COAST |
|---|---|---|---|---|---|
| `carbonintensity.org.uk` (default) | Great Britain | Postcode outward code, e.g. `OX1` | 30 min, 47 h | None | Yes |
| `wattnet.eu` | Most of Europe | Zone code, e.g. `DE`, `FR`, `SE1`, `IT_NORTH` | 15 min, 72 h | Email and password | Yes (experimental service) |
| `energy-charts.info` | Continental Europe | Bidding zone, e.g. `DE-LU` | 15 min | None | No: its default metric is electricity price |
| `octopus.energy` | Great Britain | Region letter, e.g. `C` | 30 min | None | No: electricity price |

Only providers whose default metric is carbon intensity work with COAST.
`multicats schedule` has no option to choose another metric, so it always
gets the provider's default, and COAST's cost function assumes gCO2/kWh.
`cats --list-providers` shows each provider's metrics, with the default
marked.

To list the valid locations for a provider:

```bash
/opt/multicats/bin/cats --list-locations wattnet.eu
```

### Example: switching to wattnet.eu

wattnet.eu needs an account; see <https://docs.wattnet.eu>. CATS reads the
credentials from the `CATS_WATTNET_EMAIL` and `CATS_WATTNET_PASSWORD`
environment variables.

1. Put them in a file only root can read:

   ```bash
   sudo install -d -m 700 /etc/multicats
   sudo install -m 600 /dev/null /etc/multicats/wattnet.env
   sudoedit /etc/multicats/wattnet.env
   ```

   ```
   CATS_WATTNET_EMAIL=you@example.org
   CATS_WATTNET_PASSWORD='...'
   ```

   Keep the single quotes if the password has spaces or shell special
   characters; both systemd and `sh` accept them.

2. Test it, with at least one held job in the partition (the provider is
   only contacted when there are jobs to schedule):

   ```bash
   sudo sh -c 'set -a; . /etc/multicats/wattnet.env; exec /opt/multicats/bin/multicats schedule --api wattnet.eu --location DE --dry-run'
   ```

3. When you set up the timer in step 6, pass the same options and load the
   credentials file:
   - **systemd:** add `EnvironmentFile=/etc/multicats/wattnet.env` to the
     `[Service]` section, and add `--api wattnet.eu --location DE` to
     `ExecStart`.
   - **cron:** wrap the command the same way as the test above:
     `sh -c 'set -a; . /etc/multicats/wattnet.env; exec /opt/multicats/bin/multicats schedule --api wattnet.eu --location DE'`.

### What changes with the provider

- **Slot length.** COAST's slots follow the provider's resolution: 30 min
  for `carbonintensity.org.uk`, 15 min for `wattnet.eu`. Running
  `multicats schedule` every 5 minutes is fine for both.
- **Horizon.** COAST looks at most 48 h ahead, however long the forecast. A
  shorter forecast shortens the horizon, and jobs longer than the forecast
  are left held (see step 1).
- **Weights.** The default COAST weights and reference constants were
  calibrated on GB carbon intensity. The carbon term grows in proportion to the grid's intensity, so
  on a grid with a very different typical intensity it counts for more or
  less against delay and congestion. To keep the paper's balance, scale
  `--carbon-weight` by GB's average intensity divided by yours: for a grid
  averaging twice GB's intensity, halve it.
- **Errors.** A wrong location or missing credentials makes the run fail with
  a traceback naming the problem (`InvalidLocationError`,
  `ProviderAuthenticationError`), and leaves every job held. Because the
  provider is only contacted when there are held jobs, such a mistake only
  shows up once a job arrives, so do the dry run above.

## 5. Try `multicats schedule` by hand

`--scheduler` picks the scheduling algorithm. These are available:

| Scheduler | What it does | Needs a carbon forecast |
|---|---|---|
| `coast` (default) | Low-carbon start times, spread out to limit congestion | Yes |
| `random` | Uniformly random start times; a baseline for comparison | No |

Each scheduler has its own options, defined in the scheduler's own module
under [multicats/schedulers/](multicats/schedulers/). Options for schedulers
you didn't pick are ignored. `multicats schedule --help` is the authoritative
list for the version you have installed: it shows the general options, then
one group of options per scheduler.

General options:

| Option | Default | Meaning |
|---|---|---|
| `--scheduler` | `coast` | Scheduling algorithm: `coast` or `random` |
| `--partition` | `multicats` | Partition to schedule |
| `--max-wait-hours` | `24` | Longest a job may be delayed past submission (`m`) |
| `--dry-run` | off | Log assignments without changing Slurm |

COAST options:

| Option | Default | Meaning |
|---|---|---|
| `--location` | (required) | Grid location passed to the carbon-intensity provider |
| `--api` | `carbonintensity.org.uk` | CATS provider name; see [step 4](#4-choose-a-carbon-intensity-provider) |
| `--carbon-weight` | `4.0` | Carbon weight (alpha) |
| `--delay-weight` | `1.0` | Delay weight (beta) |
| `--congestion-weight` | `0.0005` | Congestion weight (gamma) |

The default weights are the ones chosen in the COAST paper. The other COAST
settings, such as the reference constants and the 48 h horizon, use the
defaults in `CoastConfig`
([multicats/schedulers/coast.py](multicats/schedulers/coast.py)).

Random options:

| Option | Default | Meaning |
|---|---|---|
| `--window-hours` | `48` | Latest start, in hours after submission |
| `--seed` | none | Random seed, for reproducible runs |

Settings that have no option use the defaults in the scheduler's config class
(`CoastConfig` or `RandomConfig`).

Submit a test job to the partition, then do a dry run:

```bash
sudo /opt/multicats/bin/multicats schedule --location OX1 --dry-run
```

It logs which scheduler it used, one line per held job, then a summary:

```
2026-10-18 17:35:53,790 multicats INFO scheduling 3 held jobs with coast
2026-10-18 17:35:53,808 multicats INFO job 1: start 2026-10-18T18:00:00+00:00
2026-10-18 17:35:53,819 multicats INFO assigned 3 of 3 held jobs
```

Run it again without `--dry-run`, and check that `squeue` shows the new start
times. A third run should log `no held jobs in partition multicats`.

If a scheduler's required options are missing, `multicats schedule` exits
with status 2 before touching Slurm, for example
`multicats schedule: error: the coast scheduler needs --location`.

To try the baseline instead, which needs no forecast or network access:

```bash
sudo /opt/multicats/bin/multicats schedule --scheduler random --window-hours 6 --dry-run
```

## 6. Run it every few minutes

Run `multicats schedule` more often than COAST's slot length. The GB
provider's forecast has 30-minute slots, so every 5 minutes is plenty: a job
then waits at most 5 minutes before it gets a start time.

The examples below run COAST with its default settings. To use another
scheduler or change its settings, add the options to the command, for example
`--scheduler random --window-hours 6` or `--congestion-weight 0.001`. For
systemd, run `sudo systemctl daemon-reload` after editing the unit; cron picks
up changes to `/etc/cron.d/multicats` on its own. Changes only affect jobs
that are still held: jobs that already have a start time keep it.

### systemd timer (recommended)

`/etc/systemd/system/multicats-schedule.service`:

```ini
[Unit]
Description=MultiCATS microbatch: assign low-carbon start times
After=network-online.target slurmctld.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/multicats/bin/multicats schedule --location OX1
Environment=TZ=UTC
```

`/etc/systemd/system/multicats-schedule.timer`:

```ini
[Unit]
Description=Run MultiCATS every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now multicats-schedule.timer
```

A oneshot service never overlaps itself: if a run is still going when the
timer fires, systemd skips that start.

### cron (alternative)

`/etc/cron.d/multicats`:

```
*/5 * * * * root TZ=UTC flock -n /run/multicats-schedule.lock /opt/multicats/bin/multicats schedule --location OX1 >> /var/log/multicats.log 2>&1
```

`flock -n` skips a run if the previous one is still going. Add
`/var/log/multicats.log` to logrotate.

## 7. Monitor it

**Is the task running?**

```bash
systemctl list-timers multicats-schedule.timer   # last and next run
systemctl status multicats-schedule.service      # result of the last run
```

**What did it decide?** Each run logs the scheduler it used, every
assignment, every job it could not place, and a summary line:

```bash
journalctl -u multicats-schedule.service --since today
journalctl -u multicats-schedule.service -f          # follow live
journalctl -u multicats-schedule.service -p warning  # only problems
# cron: tail -f /var/log/multicats.log
```

A failed run (for example, the carbon API being unreachable) exits with a
Python traceback, shows as `failed` in `systemctl status`, and leaves every
job held. The next run tries again.

**What does the queue look like?**

```bash
squeue --partition=multicats \
  --Format="JobID:10,UserName:10,StateCompact:4,SubmitTime:21,EligibleTime:21,StartTime:21,Reason:12"
```

`ELIGIBLE_TIME` is the job's begin time, the field MultiCATS sets: about a
year out means the job is still waiting for MultiCATS, anything else is the
time MultiCATS assigned. `START_TIME` is when Slurm expects the job to start.
It equals the begin time on an idle cluster, but is later when the job has
to wait for resources (`REASON` `Resources` or `Priority`).

**What is the plugin doing?** The plugin logs one line per held job to the
slurmctld log, set by `SlurmctldLogFile` in `slurm.conf`:

```bash
grep "multicats: holding job" /var/log/slurm/slurmctld.log
```

**What to alert on:**

- **Jobs held for more than two runs.** The task has stopped, or is failing.
- **`no feasible start in forecast` warnings.** These jobs are longer than
  the forecast; lower the partition's `MaxTime`, or release them by hand.
- **`failed` state on `multicats-schedule.service`.**

## Releasing jobs by hand

To start a held job now, for example during an outage or for a job the
scheduler could not place:

```bash
sudo scontrol update JobId=<id> StartTime=now
```

To stop using MultiCATS:

1. Disable the timer:

   ```bash
   sudo systemctl disable --now multicats-schedule.timer
   ```

2. Release every held job:

   ```bash
   squeue -h -p multicats -t PD -o %i | xargs -r -I{} sudo scontrol update JobId={} StartTime=now
   ```

3. Remove `lua` from `JobSubmitPlugins`, or the partition, and restart
   slurmctld.

## Known limitations

- **No daemon or config file yet.** Settings are `multicats schedule`'s
  command-line options; scheduler settings without an option use their config
  class defaults (`CoastConfig`, `RandomConfig`).
- **Only the provider's default metric.** COAST can only use providers whose
  default metric is carbon intensity; see
  [step 4](#4-choose-a-carbon-intensity-provider).
- **Schedulers are built in.** `--scheduler` offers the schedulers registered
  in `SCHEDULERS` in
  [multicats/schedulers/\_\_init\_\_.py](multicats/schedulers/__init__.py).
  Adding one means changing MultiCATS itself; there is no plugin mechanism.
- **Each job is scheduled once.** A job keeps its start time even if a later
  forecast would move it.
- **Energy estimates are rough.** With no energy accounting, energy comes from
  a fixed watts-per-CPU estimate (`SlurmCLIConfig.watts_per_cpu`).
- **Start times must be UTC.** See [Requirements](#requirements).
