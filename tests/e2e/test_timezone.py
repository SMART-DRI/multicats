"""E2e tests for start times when MultiCATS runs outside UTC.

Slurm's client commands read and print times in the local time zone of the
process that runs them. These tests run MultiCATS with a non-UTC ``TZ`` and
check that start times still round-trip through Slurm unchanged.
"""

import os
import re
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from multicats.cli.schedule import held_job_ids
from multicats.controllers.slurm import SlurmController
from multicats.schedulers.coast import slot_origin

from tests.e2e.conftest import submit_held_jobs

pytestmark = pytest.mark.e2e

# A POSIX TZ string: UTC+05:30 all year, and needs no tzdata.
LOCAL_TZ = "IST-5:30"
LOCAL_OFFSET = timedelta(hours=5, minutes=30)


@pytest.fixture
def local_tz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", LOCAL_TZ)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def held_job() -> Iterator[str]:
    [job_id] = submit_held_jobs(1)
    yield job_id
    SlurmController().cancel(job_id)


@pytest.mark.usefixtures("local_tz")
def test_release_round_trips_outside_utc(held_job: str) -> None:
    controller = SlurmController()
    start = slot_origin(datetime.now(tz=UTC), timedelta(minutes=30)) + timedelta(
        hours=3
    )

    controller.release(held_job, start)

    assert controller.begin_time(held_job) == start
    assert held_job not in held_job_ids("multicats")

    # scontrol run from a local-time shell shows the same instant in local time
    show = subprocess.run(
        ["scontrol", "show", "job", held_job],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert os.environ["TZ"] == LOCAL_TZ
    m = re.search(r"EligibleTime=(\S+)", show)
    assert m is not None
    assert m.group(1) == (start + LOCAL_OFFSET).strftime("%Y-%m-%dT%H:%M:%S")


@pytest.mark.usefixtures("local_tz")
def test_held_job_still_held_outside_utc(held_job: str) -> None:
    assert held_job in held_job_ids("multicats")
