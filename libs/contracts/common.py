from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ErrorBody(BaseModel):
    code: str = Field(...)
    message: str = Field(...)
    service: str = Field(...)
    state: Optional[str] = Field(default=None)
    retryable: bool = Field(default=False)
    details: dict[str, Any] = Field(default_factory=dict)


class ProjectScoped(BaseModel):
    project_id: Optional[str] = None


class Envelope(BaseModel, Generic[T]):
    success: bool = Field(...)
    trace_id: str = Field(...)
    data: Optional[T] = Field(default=None)
    error: Optional[ErrorBody] = Field(default=None)


class HealthData(BaseModel):
    service: str
    status: str = "ok"
    version: str = "0.1.0"
    now: str = Field(default_factory=utcnow_iso)


class ProgressInfo(BaseModel):
    step: int = 0
    total_steps: int = 0
    message: str = ""


class TaskEvent(BaseModel):
    time: str
    service: str
    task_id: str
    status: str
    state: str
    level: str = "info"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
