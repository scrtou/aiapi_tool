from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.contracts.mail import CreateMailAccountRequest, CreateMailAccountData
from libs.core.auth import require_access
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/mail/accounts", tags=["mail-accounts"])


@router.get("", dependencies=[Depends(require_access("mail:read"))])
def list_accounts(request: Request, provider: str | None = None, status: str | None = None, limit: int = 50):
    service = request.app.state.mail_service
    data = service.list_accounts(
        provider=provider,
        status=status,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
        limit=limit,
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"), status_code=200)


@router.post("", dependencies=[Depends(require_access("mail:create"))])
def create_account(request: Request, payload: CreateMailAccountRequest):
    service = request.app.state.mail_service
    account = service.create_account(payload, project_id=current_project_id(request))
    return success_response(request.state.trace_id, CreateMailAccountData(account=account).model_dump(mode="json"), status_code=200)


@router.get("/{account_id}", dependencies=[Depends(require_access("mail:read"))])
def get_account(request: Request, account_id: str):
    service = request.app.state.mail_service
    account = service.get_account(
        account_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, {"account": account.model_dump(mode="json")})


@router.delete("/{account_id}", dependencies=[Depends(require_access("mail:delete"))])
def delete_account(request: Request, account_id: str):
    service = request.app.state.mail_service
    data = service.delete_account(
        account_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))
