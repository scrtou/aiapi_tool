from __future__ import annotations

from typing import Any, Optional


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        service: str,
        state: Optional[str] = None,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
        status_code: int = 500,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.service = service
        self.state = state
        self.retryable = retryable
        self.details = details or {}
        self.status_code = status_code
