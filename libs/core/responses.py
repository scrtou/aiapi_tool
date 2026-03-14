from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from libs.contracts.common import Envelope, ErrorBody
from libs.core.exceptions import ServiceError


def success_response(trace_id: str, data: Any, status_code: int = 200) -> JSONResponse:
    payload = Envelope(success=True, trace_id=trace_id, data=data, error=None)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def error_response(trace_id: str, error: ServiceError) -> JSONResponse:
    payload = Envelope(
        success=False,
        trace_id=trace_id,
        data=None,
        error=ErrorBody(
            code=error.code,
            message=error.message,
            service=error.service,
            state=error.state,
            retryable=error.retryable,
            details=error.details,
        ),
    )
    return JSONResponse(status_code=error.status_code, content=payload.model_dump(mode="json"))
