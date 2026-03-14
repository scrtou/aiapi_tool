from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.contracts.mail import ExtractConfirmationLinkRequest
from libs.core.auth import require_access
from libs.core.request_context import allow_cross_project, current_project_id
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/mail/accounts/{account_id}", tags=["mail-messages"])


@router.get("/messages", dependencies=[Depends(require_access("mail:read"))])
def list_messages(request: Request, account_id: str):
    service = request.app.state.mail_service
    data = service.list_messages(
        account_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.get("/messages/{message_id}", dependencies=[Depends(require_access("mail:read"))])
def get_message(request: Request, account_id: str, message_id: str):
    service = request.app.state.mail_service
    data = service.get_message(
        account_id,
        message_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))


@router.post("/extract-confirmation-link", dependencies=[Depends(require_access("mail:read"))])
def extract_confirmation_link(request: Request, account_id: str, payload: ExtractConfirmationLinkRequest):
    service = request.app.state.mail_service
    data = service.extract_confirmation_link(
        account_id,
        payload.message_id,
        project_id=current_project_id(request),
        allow_cross_project=allow_cross_project(request),
    )
    return success_response(request.state.trace_id, data.model_dump(mode="json"))
