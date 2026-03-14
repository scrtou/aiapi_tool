from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.contracts.login import VerifySessionRequest
from libs.core.auth import require_access
from libs.core.responses import success_response


router = APIRouter(prefix="/api/v1/logins", tags=["logins"])


@router.post("/verify-session", dependencies=[Depends(require_access("login:verify"))])
def verify_session(request: Request, payload: VerifySessionRequest):
    service = request.app.state.login_service
    data = service.verify_session(payload)
    return success_response(request.state.trace_id, data.model_dump(mode="json"))
