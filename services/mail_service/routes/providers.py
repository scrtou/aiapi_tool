from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.core.auth import require_internal_or_admin
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/mail/providers", tags=["mail-providers"])


@router.get("", dependencies=[Depends(require_internal_or_admin())])
def list_providers(request: Request):
    service = request.app.state.mail_service
    data = service.list_providers()
    return success_response(request.state.trace_id, data)


@router.get("/{provider_name}/domains", dependencies=[Depends(require_internal_or_admin())])
def get_domains(request: Request, provider_name: str):
    service = request.app.state.mail_service
    data = service.get_provider_domains(provider_name)
    return success_response(request.state.trace_id, data)


@router.post("/{provider_name}/health-check", dependencies=[Depends(require_internal_or_admin())])
def health_check(request: Request, provider_name: str):
    service = request.app.state.mail_service
    data = service.check_provider_health(provider_name)
    return success_response(request.state.trace_id, data)


@router.post("/{provider_name}/enable", dependencies=[Depends(require_internal_or_admin())])
def enable_provider(request: Request, provider_name: str):
    service = request.app.state.mail_service
    data = service.set_provider_enabled(provider_name, True)
    return success_response(request.state.trace_id, data)


@router.post("/{provider_name}/disable", dependencies=[Depends(require_internal_or_admin())])
def disable_provider(request: Request, provider_name: str):
    service = request.app.state.mail_service
    data = service.set_provider_enabled(provider_name, False)
    return success_response(request.state.trace_id, data)
