from __future__ import annotations

from contextvars import ContextVar
import secrets

_current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


def generate_trace_id(prefix: str = "trc") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def generate_task_id(prefix: str = "tsk") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def set_current_trace_id(trace_id: str | None):
    _current_trace_id.set(trace_id)


def get_current_trace_id() -> str | None:
    return _current_trace_id.get()
