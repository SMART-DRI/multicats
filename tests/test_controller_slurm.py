"""Unit tests for SlurmController — no Slurm installation required."""

import subprocess
from datetime import UTC, datetime

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


def _fake_scontrol(stdout: str):
    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
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
