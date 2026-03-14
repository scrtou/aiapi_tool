from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.contracts.registration import CreateRegistrationTaskRequest
from libs.core.auth import require_internal_or_admin
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/registrations/tasks", tags=["registration-tasks"])


@router.get("", dependencies=[Depends(require_internal_or_admin())])
def list_tasks(request: Request, status: str | None = None, state: str | None = None, site: str | None = None, limit: int = 50):
    service = request.app.state.registration_service
    data = service.list_tasks(
        status=status,
        state=state,
        site=site,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
        limit=limit,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"), status_code=200)


@router.post("", dependencies=[Depends(require_internal_or_admin())])
def create_task(request: Request, payload: CreateRegistrationTaskRequest):
    service = request.app.state.registration_service
    data = service.create_task(payload, project_id=current_project_id(request))
    return success_response(request.state.trace_id, data.model_dump(mode="json"), status_code=200)


@router.get("/{task_id}", dependencies=[Depends(require_internal_or_admin())])
def get_task(request: Request, task_id: str):
    service = request.app.state.registration_service
    data = service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"), status_code=200)


@router.post("/{task_id}/cancel", dependencies=[Depends(require_internal_or_admin())])
def cancel_task(request: Request, task_id: str):
    service = request.app.state.registration_service
    data = service.cancel_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"), status_code=200)
