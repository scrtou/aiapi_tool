from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.core.auth import require_internal_or_admin
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/events", tags=["workflow-events"])


@router.get("/{task_id}", dependencies=[Depends(require_internal_or_admin())])
def list_events(request: Request, task_id: str):
    service = request.app.state.orchestrator_service
    detail = service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, {"events": [event.model_dump(mode="json") if hasattr(event, 'model_dump') else event for event in detail.events]})
