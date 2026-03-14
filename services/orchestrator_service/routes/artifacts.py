from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.core.artifacts import build_file_response
from libs.core.auth import require_internal_or_admin
from libs.core.exceptions import ServiceError
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response
from libs.core.sqlite import SQLiteArtifactStore


router = APIRouter(prefix="/api/v1/artifacts", tags=["workflow-artifacts"])


@router.get("/{task_id}", dependencies=[Depends(require_internal_or_admin())])
def list_artifacts(request: Request, task_id: str):
    request.app.state.orchestrator_service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    store = SQLiteArtifactStore("orchestrator_service")
    artifacts = store.list_for_task(task_id)
    if not artifacts:
        workflow_payload = request.app.state.orchestrator_service.store.get_task(task_id) or {}
        result = workflow_payload.get("result") or {}
        registration_result = result.get("registration") or {}
        artifacts = registration_result.get("artifacts", []) or artifacts
        registration_task_id = workflow_payload.get("registration_task_id")
        if not artifacts and registration_task_id:
            reg_store = SQLiteArtifactStore("registration_service")
            artifacts = reg_store.list_for_task(registration_task_id)
    return success_response(request.state.trace_id, {"artifacts": artifacts})


@router.get("/{task_id}/{artifact_name}", dependencies=[Depends(require_internal_or_admin())])
def get_artifact(request: Request, task_id: str, artifact_name: str):
    request.app.state.orchestrator_service.get_task(
        task_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    # 先查 workflow 自己的 artifacts，再回退到 registration_service
    workflow_store = SQLiteArtifactStore("orchestrator_service")
    artifact = workflow_store.get_by_name(task_id, artifact_name)
    if not artifact:
        workflow_payload = request.app.state.orchestrator_service.store.get_task(task_id) or {}
        result = workflow_payload.get("result") or {}
        registration_result = result.get("registration") or {}
        for item in registration_result.get("artifacts", []) or []:
            if item.get("name") == artifact_name:
                artifact = item
                break
    if not artifact:
        workflow_payload = request.app.state.orchestrator_service.store.get_task(task_id) or {}
        registration_task_id = workflow_payload.get("registration_task_id") or task_id
        reg_store = SQLiteArtifactStore("registration_service")
        artifact = reg_store.get_by_name(registration_task_id, artifact_name)
    if not artifact:
        raise ServiceError(
            code="RESOURCE_NOT_FOUND",
            message=f"artifact not found: {artifact_name}",
            service="orchestrator-service",
            state="get_artifact",
            status_code=404,
        )
    return build_file_response(
        artifact["storage_path"],
        filename=f"{artifact['name']}",
        media_type=artifact.get("mime_type"),
    )
