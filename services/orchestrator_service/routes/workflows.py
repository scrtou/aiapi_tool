from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from libs.contracts.workflow import LoginWorkflowRequest, RegisterWorkflowRequest
from libs.core.auth import require_access
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.get("", dependencies=[Depends(require_access("workflow:read"))])
def list_workflows(request: Request, status: str | None = None, state: str | None = None, site: str | None = None, limit: int = 50):
    service = request.app.state.orchestrator_service
    data = service.list_tasks(
        status=status,
        state=state,
        site=site,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
        limit=limit,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/register-and-login", dependencies=[Depends(require_access("workflow:run"))])
def register_and_login(request: Request, payload: RegisterWorkflowRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    service = request.app.state.orchestrator_service
    data = service.create_register_and_login_task(
        payload,
        project_id=current_project_id(request),
        idempotency_key=idempotency_key,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/register", dependencies=[Depends(require_access("workflow:run"))])
def register(request: Request, payload: RegisterWorkflowRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    service = request.app.state.orchestrator_service
    data = service.create_register_task(
        payload,
        project_id=current_project_id(request),
        idempotency_key=idempotency_key,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/login", dependencies=[Depends(require_access("workflow:run"))])
def login(request: Request, payload: LoginWorkflowRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    service = request.app.state.orchestrator_service
    data = service.create_login_task(
        payload,
        project_id=current_project_id(request),
        idempotency_key=idempotency_key,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/{task_id}/retry", dependencies=[Depends(require_access("workflow:run"))])
def retry_workflow(request: Request, task_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    service = request.app.state.orchestrator_service
    data = service.retry_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
        idempotency_key=idempotency_key,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/{task_id}/cancel", dependencies=[Depends(require_access("workflow:run"))])
def cancel_workflow(request: Request, task_id: str):
    service = request.app.state.orchestrator_service
    data = service.cancel_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.get("/{task_id}", dependencies=[Depends(require_access("workflow:read"))])
def get_workflow(request: Request, task_id: str):
    service = request.app.state.orchestrator_service
    data = service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))
