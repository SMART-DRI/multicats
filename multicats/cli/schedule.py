"""``multicats schedule``: run one MultiCATS microbatch.

Assigns start times to jobs that ``job_submit.lua`` is still holding in the
MultiCATS partition, and leaves jobs that already have a start time alone. Run
it every few minutes from a systemd timer or cron; see DEPLOYMENT.md.
"""

import argparse
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from ..controllers.slurm import SlurmController, SlurmControllerConfig
from ..interfaces import Controller, QueueProvider, Scheduler
from ..providers.slurm_cli import SlurmCLIConfig, SlurmCLIProvider
from ..schedulers import DEFAULT_SCHEDULER, SCHEDULERS
from ..slurm import run_slurm
from .base import Command, UsageError

# job_submit.lua sets BeginTime a year out; a job starting further out than
# this is still waiting for MultiCATS.
HELD_AFTER = timedelta(days=300)

log = logging.getLogger("multicats")


def parse_held(squeue_output: str, now: datetime) -> set[str]:
    """Job IDs from ``squeue --Format=JobID,EligibleTime`` output that are
    still held, i.e. whose begin time is still about a year out.

    Slurm prints times without a time zone; they are UTC because
    :func:`~multicats.slurm.run_slurm` runs squeue with ``TZ=UTC``.
    """
    cutoff = now + HELD_AFTER
    held: set[str] = set()
    for line in squeue_output.splitlines():
        if not line.strip():
            continue
        job_id, begin = line.split()
        if begin in ("N/A", "Unknown"):
            continue
        if datetime.fromisoformat(begin).replace(tzinfo=UTC) > cutoff:
            held.add(job_id)
    return held


def held_job_ids(partition: str) -> set[str]:
    """IDs of pending jobs in ``partition`` that have no assigned start yet."""
    out = run_slurm(
        [
            "squeue",
            "--noheader",
            "--states=PENDING",
            f"--partition={partition}",
            "--Format=JobID,EligibleTime",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return parse_held(out, datetime.now(tz=UTC))


def schedule_held(
    held: set[str],
    provider: QueueProvider,
    scheduler: Scheduler,
    controller: Controller,
    dry_run: bool = False,
) -> Mapping[str, datetime | None]:
    """Assign start times to the held jobs and release them.

    Jobs the scheduler rejects (``None``) stay held.
    """
    if not held:
        log.info("no held jobs")
        return {}
    jobs = [j for j in provider.pending_jobs() if j.job_id in held]
    assignments = scheduler.assign(jobs, provider.background_load())
    for job_id, start in sorted(assignments.items()):
        if start is None:
            log.warning("job %s: no feasible start in forecast, left held", job_id)
            continue
        log.info("job %s: start %s", job_id, start.isoformat())
        if not dry_run:
            controller.release(job_id, start)
    log.info(
        "assigned %d of %d held jobs",
        sum(s is not None for s in assignments.values()),
        len(jobs),
    )
    return assignments


def add_arguments(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument(
        "--scheduler",
        choices=sorted(SCHEDULERS),
        default=DEFAULT_SCHEDULER,
        help="scheduling algorithm (default: %(default)s)",
    )
    _ = parser.add_argument(
        "--partition", default="multicats", help="partition to schedule"
    )
    _ = parser.add_argument(
        "--max-wait-hours",
        type=float,
        default=24.0,
        help="longest a job may be delayed past submission (default: %(default)s)",
    )
    _ = parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log assignments without changing Slurm",
    )
    for name, scheduler in SCHEDULERS.items():
        scheduler.add_arguments(
            parser.add_argument_group(f"{name} options", scheduler.description)
        )


def build_scheduler(args: argparse.Namespace) -> Scheduler:
    """Build the scheduler chosen with ``--scheduler``.

    Raises UsageError if its options are incomplete.
    """
    try:
        return SCHEDULERS[args.scheduler].from_args(args)
    except ValueError as e:
        raise UsageError(str(e)) from e


def run(args: argparse.Namespace) -> None:
    scheduler = build_scheduler(args)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    held = held_job_ids(args.partition)
    if not held:
        log.info("no held jobs in partition %s", args.partition)
        return
    log.info("scheduling %d held jobs with %s", len(held), args.scheduler)
    provider = SlurmCLIProvider(
        SlurmCLIConfig(default_max_wait=timedelta(hours=args.max_wait_hours))
    )
    _ = schedule_held(
        held,
        provider,
        scheduler,
        SlurmController(SlurmControllerConfig()),
        args.dry_run,
    )


COMMAND = Command(
    name="schedule",
    help="run one microbatch: assign start times to jobs held in the "
    + "MultiCATS partition",
    add_arguments=add_arguments,
    run=run,
)
