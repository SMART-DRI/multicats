"""Unit tests for SlurmController — no Slurm installation required."""

import subprocess
from datetime import UTC, datetime, timedelta, timezone

import pytest
from multicats.controllers.slurm import SlurmController

# scontrol show job for a released job waiting on resources: the begin time
# (EligibleTime) is what MultiCATS set, StartTime is Slurm's later estimate.
SHOW_JOB = """\
JobId=4 JobName=wrap
   JobState=PENDING Reason=Resources Dependency=(null)
   SubmitTime=2026-10-19T13:58:05 EligibleTime=2026-10-19T14:00:05
   StartTime=2026-10-19T14:17:42 EndTime=2026-10-19T14:47:42 Deadline=N/A
"""


def _fake_scontrol(
    stdout: str, calls: list[tuple[list[str], dict[str, str]]] | None = None
):
    def run(
        args: list[str], env: dict[str, str] | None = None, **_: object
    ) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append((args, env or {}))
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return run


def test_begin_time_reads_eligible_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_scontrol(SHOW_JOB))
    assert SlurmController().begin_time("4") == datetime(
        2026, 10, 19, 14, 0, 5, tzinfo=UTC
    )


def test_begin_time_none_when_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = SHOW_JOB.replace(
        "EligibleTime=2026-10-19T14:00:05", "EligibleTime=Unknown"
    )
    monkeypatch.setattr(subprocess, "run", _fake_scontrol(stdout))
    assert SlurmController().begin_time("4") is None


def test_begin_time_runs_scontrol_in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setenv("TZ", "Europe/London")
    monkeypatch.setattr(subprocess, "run", _fake_scontrol(SHOW_JOB, calls))
    _ = SlurmController().begin_time("4")
    assert [env["TZ"] for _, env in calls] == ["UTC"]


def test_release_sends_utc_start(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setenv("TZ", "Europe/London")
    monkeypatch.setattr(subprocess, "run", _fake_scontrol("", calls))
    bst = timezone(timedelta(hours=1))
    SlurmController().release("4", datetime(2099, 7, 1, 15, 0, tzinfo=bst))
    [(args, env)] = calls
    assert args[-1] == "StartTime=2099-07-01T14:00:00"
    assert env["TZ"] == "UTC"
