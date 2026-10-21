"""Unit tests for the multicats CLI — no Slurm installation required."""

import argparse
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from typing_extensions import override

from multicats.cli import COMMANDS, UsageError, main, parser
from multicats.cli.schedule import build_scheduler, parse_held, schedule_held
from multicats.interfaces import Controller, QueueProvider, Scheduler
from multicats.models import JobParams
from multicats.schedulers import SCHEDULERS, CoastScheduler
from multicats.schedulers.random import RandomConfig, RandomScheduler

NOW = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)
START = datetime(2026, 10, 18, 17, 30, tzinfo=UTC)


def make_job(job_id: str) -> JobParams:
    return JobParams(
        job_id=job_id,
        arrival=NOW,
        resource=1.0,
        duration=timedelta(minutes=30),
        energy=0.01,
        deadline=NOW + timedelta(hours=25),
        max_wait=timedelta(hours=24),
    )


class FakeQueue(QueueProvider):
    def __init__(self, job_ids: list[str]) -> None:
        self.jobs: list[JobParams] = [make_job(j) for j in job_ids]

    @override
    def background_load(self) -> float:
        return 0.0

    @override
    def pending_jobs(self) -> list[JobParams]:
        return self.jobs

    @override
    def energy_estimate(self, job_id: str) -> float:
        return 0.01


class FixedScheduler(Scheduler):
    def __init__(self, result: dict[str, datetime | None]) -> None:
        self.result: dict[str, datetime | None] = result
        self.seen: list[str] = []

    @override
    def assign(
        self, jobs: list[JobParams], background_load: float
    ) -> Mapping[str, datetime | None]:
        self.seen = [j.job_id for j in jobs]
        return {j.job_id: self.result[j.job_id] for j in jobs}


class RecordingController(Controller):
    def __init__(self) -> None:
        self.released: dict[str, datetime] = {}

    @override
    def hold(self, job_id: str) -> None:
        pass

    @override
    def release(self, job_id: str, start: datetime) -> None:
        self.released[job_id] = start

    @override
    def cancel(self, job_id: str) -> None:
        pass


# parse_held


def test_parse_held_keeps_far_future_starts() -> None:
    out = "1 2027-10-18T17:00:00\n2 2026-10-18T17:30:00\n"
    assert parse_held(out, NOW) == {"1"}


@pytest.mark.parametrize("start", ["N/A", "Unknown"])
def test_parse_held_skips_unknown_start(start: str) -> None:
    assert parse_held(f"7 {start}\n", NOW) == set()


def test_parse_held_empty_output() -> None:
    assert parse_held("", NOW) == set()
    assert parse_held("\n", NOW) == set()


# schedule_held


def test_schedule_held_only_schedules_held_jobs() -> None:
    scheduler = FixedScheduler({"1": START})
    controller = RecordingController()
    _ = schedule_held({"1"}, FakeQueue(["1", "2"]), scheduler, controller)
    assert scheduler.seen == ["1"]
    assert controller.released == {"1": START}


def test_schedule_held_leaves_rejected_jobs_held() -> None:
    controller = RecordingController()
    result = schedule_held(
        {"1", "2"},
        FakeQueue(["1", "2"]),
        FixedScheduler({"1": START, "2": None}),
        controller,
    )
    assert result == {"1": START, "2": None}
    assert controller.released == {"1": START}


def test_schedule_held_dry_run_releases_nothing() -> None:
    controller = RecordingController()
    result = schedule_held(
        {"1"}, FakeQueue(["1"]), FixedScheduler({"1": START}), controller, dry_run=True
    )
    assert result == {"1": START}
    assert controller.released == {}


def test_schedule_held_with_no_held_jobs() -> None:
    scheduler = FixedScheduler({})
    assert (
        schedule_held(set(), FakeQueue(["1"]), scheduler, RecordingController()) == {}
    )
    assert scheduler.seen == []


# arguments


def test_scheduler_defaults_to_coast() -> None:
    args = parser().parse_args(["schedule", "--location", "OX1"])
    assert args.scheduler == "coast"
    assert args.partition == "multicats"
    assert not args.dry_run


def test_coast_weights_default_to_coast_config() -> None:
    args = parser().parse_args(["schedule", "--location", "OX1"])
    assert (args.carbon_weight, args.delay_weight, args.congestion_weight) == (
        4.0,
        1.0,
        0.0005,
    )


def test_coast_needs_location() -> None:
    args = parser().parse_args(["schedule"])
    with pytest.raises(UsageError, match="--location"):
        _ = build_scheduler(args)


def test_main_rejects_coast_without_location() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["schedule"])
    assert exc.value.code == 2


def test_main_requires_a_command() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_every_command_is_a_subcommand() -> None:
    [subparsers] = [
        a
        for a in parser()._actions  # pyright: ignore[reportPrivateUsage]
        if isinstance(a, argparse._SubParsersAction)  # pyright: ignore[reportPrivateUsage]
    ]
    assert set(subparsers.choices) == set(COMMANDS)


def test_random_scheduler_needs_no_location() -> None:
    args = parser().parse_args(
        ["schedule", "--scheduler", "random", "--window-hours", "6", "--seed", "1"]
    )
    scheduler = build_scheduler(args)
    assert isinstance(scheduler, RandomScheduler)
    assert scheduler.config == RandomConfig(window=timedelta(hours=6), seed=1)


def test_unknown_scheduler_is_rejected() -> None:
    with pytest.raises(SystemExit):
        _ = parser().parse_args(["schedule", "--scheduler", "fifo"])


@pytest.mark.parametrize("name", sorted(SCHEDULERS))
def test_every_scheduler_has_an_option_group(name: str) -> None:
    schedule_parser = parser().parse_args(["schedule"])._parser
    titles = {g.title for g in schedule_parser._action_groups}  # pyright: ignore[reportPrivateUsage]
    assert f"{name} options" in titles


def test_coast_builds_from_options() -> None:
    args = parser().parse_args(
        ["schedule", "--location", "OX1", "--congestion-weight", "0.01"]
    )
    scheduler = build_scheduler(args)
    assert isinstance(scheduler, CoastScheduler)
    assert scheduler.config.location == "OX1"
    assert scheduler.config.congestion_weight == 0.01
