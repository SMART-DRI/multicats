"""Unit tests for multicats.slurm — no Slurm installation required."""

import subprocess

import pytest
from multicats.slurm import run_slurm, slurm_env


def test_slurm_env_overrides_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/London")
    monkeypatch.setenv("SLURM_CONF", "/etc/slurm/slurm.conf")
    env = slurm_env()
    assert env["TZ"] == "UTC"
    assert env["SLURM_CONF"] == "/etc/slurm/slurm.conf"


def test_run_slurm_runs_in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/London")
    result = run_slurm(["sh", "-c", "echo $TZ"], capture_output=True, check=True)
    assert result.stdout == "UTC\n"


def test_run_slurm_passes_kwargs() -> None:
    with pytest.raises(subprocess.CalledProcessError):
        _ = run_slurm(["false"], check=True)
