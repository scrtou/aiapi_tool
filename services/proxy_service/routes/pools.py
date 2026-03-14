from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.core.auth import require_internal_or_admin
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/proxy-pools", tags=["proxy-pools"])


@router.get("", dependencies=[Depends(require_internal_or_admin())])
def list_pools(request: Request):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.list_pools())


@router.post("", dependencies=[Depends(require_internal_or_admin())])
def create_pool(request: Request, payload: dict):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.create_pool(payload))


@router.delete("/{pool_id}", dependencies=[Depends(require_internal_or_admin())])
def delete_pool(request: Request, pool_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.delete_pool(pool_id))


@router.post("/{pool_id}/enable", dependencies=[Depends(require_internal_or_admin())])
def enable_pool(request: Request, pool_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.set_pool_status(pool_id, True))


@router.post("/{pool_id}/disable", dependencies=[Depends(require_internal_or_admin())])
def disable_pool(request: Request, pool_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.set_pool_status(pool_id, False))


@router.post("/{pool_id}/entries", dependencies=[Depends(require_internal_or_admin())])
def create_entry(request: Request, pool_id: str, payload: dict):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.create_pool_entry(pool_id, payload))


@router.delete("/entries/{proxy_entry_id}", dependencies=[Depends(require_internal_or_admin())])
def delete_entry(request: Request, proxy_entry_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.delete_pool_entry(proxy_entry_id))


@router.post("/entries/{proxy_entry_id}/enable", dependencies=[Depends(require_internal_or_admin())])
def enable_entry(request: Request, proxy_entry_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.set_pool_entry_status(proxy_entry_id, True))


@router.post("/entries/{proxy_entry_id}/disable", dependencies=[Depends(require_internal_or_admin())])
def disable_entry(request: Request, proxy_entry_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.set_pool_entry_status(proxy_entry_id, False))


@router.post("/entries/{proxy_entry_id}/health-check", dependencies=[Depends(require_internal_or_admin())])
def health_check_entry(request: Request, proxy_entry_id: str):
    service = request.app.state.proxy_service
    return success_response(request.state.trace_id, service.check_pool_entry_health(proxy_entry_id))
