"""Test doubles shared by unit and e2e tests."""

from datetime import datetime, timedelta
from typing import ClassVar

from cats.forecast import PointEstimate, Timeseries
from cats.providers.base import BaseProvider
from typing_extensions import override


class FakeProvider(BaseProvider):
    BASE_URL: ClassVar[str] = "https://example.invalid"

    def __init__(
        self, start: datetime, values: list[float], resolution: int = 30
    ) -> None:
        super().__init__()
        self.resolution: int = resolution
        step = timedelta(minutes=resolution)
        self.points: list[PointEstimate] = [
            PointEstimate(value=v, datetime=start + i * step)
            for i, v in enumerate(values)
        ]

    @override
    def validate_location(self, location: str | None) -> str:
        return location or ""

    @override
    def get_max_duration_minutes(self, metric: str | None = None) -> int:
        return 2820

    @override
    def get_temporal_resolution_minutes(self, metric: str | None = None) -> int:
        return self.resolution

    @override
    def get_data(
        self,
        timestamp: datetime,
        location: str | None = None,
        metric: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Timeseries:
        return Timeseries("Carbon intensity", values=self.points, unit="gCO2eq/kWh")
