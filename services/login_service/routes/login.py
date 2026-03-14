from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.contracts.login import LoginRequest
from libs.core.auth import require_access, require_internal_or_admin
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/logins", tags=["logins"])


@router.get("/results", dependencies=[Depends(require_internal_or_admin())])
def list_results(request: Request, site: str | None = None, limit: int = 50):
    service = request.app.state.login_service
    data = service.list_results(
        site=site,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
        limit=limit,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("", dependencies=[Depends(require_access("login:run"))])
def login(request: Request, payload: LoginRequest):
    service = request.app.state.login_service
    data = service.login(payload, project_id=current_project_id(request))
    return success_response(request.state.trace_id, data.model_dump(mode="json"))
