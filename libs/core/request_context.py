from __future__ import annotations

from fastapi import Request

from libs.core.auth import has_cross_project_access


def current_project_id(request: Request) -> str | None:
    return getattr(request.state, "project_id", None)


def allow_cross_project(request: Request) -> bool:
    return has_cross_project_access(request)

