from __future__ import annotations

import secrets


def generate_trace_id(prefix: str = "trc") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def generate_task_id(prefix: str = "tsk") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"
