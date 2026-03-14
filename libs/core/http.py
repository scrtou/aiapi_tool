from __future__ import annotations

from typing import Any

import requests

from libs.core.exceptions import ServiceError


class ServiceHttpClient:
    def __init__(self, service_name: str, base_url: str, internal_token: str | None = None, timeout: int = 60):
        self.service_name = service_name
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token
        self.timeout = timeout

    def _headers(self, trace_id: str, project_id: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Trace-Id": trace_id,
        }
        if self.internal_token:
            headers["X-Internal-Token"] = self.internal_token
        if project_id:
            headers["X-Project-Id"] = project_id
        return headers

    def request(self, method: str, path: str, trace_id: str, project_id: str | None = None, **kwargs) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers = {**self._headers(trace_id, project_id), **headers}
        try:
            response = requests.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        except Exception as e:
            raise ServiceError(
                code="EXTERNAL_SERVICE_ERROR",
                message=f"{self.service_name} request failed: {e}",
                service="orchestrator-service",
                state="service_request",
                retryable=True,
                details={"target_service": self.service_name, "url": url},
                status_code=503,
            )

        try:
            payload = response.json()
        except Exception:
            payload = None

        if response.status_code >= 400:
            if payload and isinstance(payload, dict) and payload.get("error"):
                error = payload["error"]
                raise ServiceError(
                    code=error.get("code", "EXTERNAL_SERVICE_ERROR"),
                    message=error.get("message", response.text[:300]),
                    service=error.get("service", self.service_name),
                    state=error.get("state"),
                    retryable=bool(error.get("retryable", False)),
                    details={**error.get("details", {}), "target_service": self.service_name},
                    status_code=response.status_code,
                )
            raise ServiceError(
                code="EXTERNAL_SERVICE_ERROR",
                message=response.text[:300],
                service="orchestrator-service",
                state="service_request",
                retryable=response.status_code >= 500,
                details={"target_service": self.service_name, "url": url, "status_code": response.status_code},
                status_code=response.status_code,
            )

        if not payload or not isinstance(payload, dict):
            raise ServiceError(
                code="EXTERNAL_SERVICE_ERROR",
                message=f"{self.service_name} returned invalid envelope",
                service="orchestrator-service",
                state="service_request",
                details={"target_service": self.service_name, "url": url},
                status_code=502,
            )

        if not payload.get("success", False):
            error = payload.get("error") or {}
            raise ServiceError(
                code=error.get("code", "EXTERNAL_SERVICE_ERROR"),
                message=error.get("message", f"{self.service_name} returned unsuccessful response"),
                service=error.get("service", self.service_name),
                state=error.get("state"),
                retryable=bool(error.get("retryable", False)),
                details={**error.get("details", {}), "target_service": self.service_name},
                status_code=502,
            )

        return payload

    def get(self, path: str, trace_id: str, project_id: str | None = None, **kwargs) -> dict[str, Any]:
        return self.request("GET", path, trace_id, project_id=project_id, **kwargs)

    def post(self, path: str, trace_id: str, project_id: str | None = None, **kwargs) -> dict[str, Any]:
        return self.request("POST", path, trace_id, project_id=project_id, **kwargs)
