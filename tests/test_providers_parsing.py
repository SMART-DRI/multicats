"""Unit tests for Slurm JSON parsing helpers — no Slurm installation required."""

from datetime import UTC, datetime, timedelta

from multicats.providers.slurm_cli import _cpus, _minutes, _ts


def slurm_ts(unix: int) -> dict[str, object]:
    return {"set": True, "infinite": False, "number": unix}


def slurm_minutes(minutes: int) -> dict[str, object]:
    return {"set": True, "infinite": False, "number": minutes}


# _ts: object format (newer Slurm)


def test_ts_parses_unix_timestamp_object() -> None:
    result = _ts(slurm_ts(0))
    assert result == datetime(1970, 1, 1, tzinfo=UTC)


def test_ts_returns_none_when_not_set() -> None:
    assert _ts({"set": False, "infinite": False, "number": 0}) is None


def test_ts_returns_none_when_infinite() -> None:
    assert _ts({"set": True, "infinite": True, "number": 0}) is None


def test_ts_returns_none_for_empty_dict() -> None:
    assert _ts({}) is None


# _ts: plain int format (older Slurm)


def test_ts_parses_plain_int() -> None:
    result = _ts(1_000_000)
    assert result == datetime.fromtimestamp(1_000_000, tz=UTC)


def test_ts_returns_none_for_zero_int() -> None:
    assert _ts(0) is None


def test_ts_returns_none_for_negative_int() -> None:
    assert _ts(-1) is None


# _minutes: object format


def test_minutes_parses_value_object() -> None:
    assert _minutes(slurm_minutes(90)) == timedelta(minutes=90)


def test_minutes_returns_none_when_not_set() -> None:
    assert _minutes({"set": False, "infinite": False, "number": 60}) is None


def test_minutes_returns_none_when_infinite() -> None:
    assert _minutes({"set": True, "infinite": True, "number": 60}) is None


def test_minutes_returns_none_for_empty_dict() -> None:
    assert _minutes({}) is None


# _minutes: plain int format


def test_minutes_parses_plain_int() -> None:
    assert _minutes(30) == timedelta(minutes=30)


def test_minutes_returns_none_for_zero_int() -> None:
    assert _minutes(0) is None


# _cpus


def test_cpus_plain_int() -> None:
    assert _cpus(4) == 4


def test_cpus_object() -> None:
    assert _cpus({"number": 8}) == 8


def test_cpus_defaults_to_one() -> None:
    assert _cpus({}) == 1
