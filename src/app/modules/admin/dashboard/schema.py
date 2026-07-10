from enum import StrEnum

from pydantic import BaseModel


class DashboardRange(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class AdminDashboardMetricRead(BaseModel):
    label: str
    value: int
    note: str


class AdminDashboardMetricsRead(BaseModel):
    range: DashboardRange
    items: list[AdminDashboardMetricRead]
