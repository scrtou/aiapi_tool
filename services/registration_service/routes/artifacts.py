from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.core.artifacts import build_file_response
from libs.core.auth import require_internal_or_admin
from libs.core.exceptions import ServiceError
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response
from libs.core.sqlite import SQLiteArtifactStore


router = APIRouter(prefix="/api/v1/artifacts", tags=["registration-artifacts"])


@router.get("/{task_id}", dependencies=[Depends(require_internal_or_admin())])
def list_artifacts(request: Request, task_id: str):
    request.app.state.registration_service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    store = SQLiteArtifactStore("registration_service")
    artifacts = store.list_for_task(task_id)
    return success_response(request.state.trace_id, {"artifacts": artifacts})


@router.get("/{task_id}/{artifact_name}", dependencies=[Depends(require_internal_or_admin())])
def get_artifact(request: Request, task_id: str, artifact_name: str):
    request.app.state.registration_service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    store = SQLiteArtifactStore("registration_service")
    artifact = store.get_by_name(task_id, artifact_name)
    if not artifact:
        raise ServiceError(
            code="RESOURCE_NOT_FOUND",
            message=f"artifact not found: {artifact_name}",
            service="registration-service",
            state="get_artifact",
            status_code=404,
        )
    return build_file_response(
        artifact["storage_path"],
        filename=f"{artifact['name']}",
        media_type=artifact.get("mime_type"),
    )
