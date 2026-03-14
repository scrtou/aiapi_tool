from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.contracts.proxy import LeaseProxyRequest
from libs.core.auth import require_access
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/proxies", tags=["proxies"])


@router.get("", dependencies=[Depends(require_access("proxy:read"))])
def list_proxies(request: Request, provider: str | None = None, status: str | None = None, limit: int = 50):
    service = request.app.state.proxy_service
    data = service.list_leases(
        provider=provider,
        status=status,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
        limit=limit,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/lease", dependencies=[Depends(require_access("proxy:lease"))])
def lease_proxy(request: Request, payload: LeaseProxyRequest):
    service = request.app.state.proxy_service
    data = service.lease(payload, project_id=current_project_id(request))
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/{proxy_id}/release", dependencies=[Depends(require_access("proxy:lease"))])
def release_proxy(request: Request, proxy_id: str):
    service = request.app.state.proxy_service
    data = service.release(
        proxy_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))
