from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

from fastapi import Request

from libs.core.config import env_str
from libs.core.exceptions import ServiceError
from libs.core.tracing import set_current_trace_id


@dataclass(frozen=True)
class ApiKeyDefinition:
    key: str
    project_id: str | None
    scopes: set[str] = field(default_factory=set)
    name: str | None = None
    is_admin: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class AuthContext:
    auth_type: str = "anonymous"
    project_id: str | None = None
    scopes: set[str] = field(default_factory=set)
    subject: str | None = None
    is_admin: bool = False

    @property
    def is_internal(self) -> bool:
        return self.auth_type == "internal"

    @property
    def is_project(self) -> bool:
        return self.auth_type == "project"

    def has_scope(self, scope: str) -> bool:
        return self.is_internal or self.is_admin or "*" in self.scopes or scope in self.scopes


def _service_name(request: Request) -> str:
    return getattr(request.app.state, "service_name", request.app.title)


def _unauthorized(request: Request, code: str, message: str, status_code: int = 401) -> ServiceError:
    return ServiceError(
        code=code,
        message=message,
        service=_service_name(request),
        state="auth",
        status_code=status_code,
    )


def _parse_api_keys() -> dict[str, ApiKeyDefinition]:
    raw = env_str("PLATFORM_API_KEYS_JSON", "[]") or "[]"
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid PLATFORM_API_KEYS_JSON: {exc}") from exc

    if isinstance(payload, dict):
        payload = [
            {
                "key": key,
                **(value if isinstance(value, dict) else {"project_id": value}),
            }
            for key, value in payload.items()
        ]

    definitions: dict[str, ApiKeyDefinition] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        scopes = item.get("scopes") or []
        if isinstance(scopes, str):
            scopes = [scopes]
        definitions[key] = ApiKeyDefinition(
            key=key,
            project_id=item.get("project_id"),
            scopes={str(scope).strip() for scope in scopes if str(scope).strip()},
            name=item.get("name"),
            is_admin=bool(item.get("is_admin", False)),
            enabled=bool(item.get("enabled", True)),
        )
    return definitions


@lru_cache(maxsize=1)
def _api_key_registry() -> dict[str, ApiKeyDefinition]:
    return _parse_api_keys()


def clear_api_key_registry_cache():
    _api_key_registry.cache_clear()


def _resolve_project_id(request: Request, definition: ApiKeyDefinition | None = None, *, internal: bool = False) -> str | None:
    header_project_id = (request.headers.get("X-Project-Id") or "").strip() or None
    if internal:
        return header_project_id or env_str("INTERNAL_SERVICE_PROJECT_ID")

    if not definition:
        return None
    if definition.is_admin:
        return header_project_id or definition.project_id
    if header_project_id and definition.project_id and header_project_id != definition.project_id:
        raise _unauthorized(request, "PROJECT_CONTEXT_INVALID", "project id header does not match api key", 403)
    return definition.project_id or header_project_id


def resolve_auth_context(request: Request) -> AuthContext:
    internal_token = env_str("INTERNAL_SERVICE_TOKEN")
    presented_internal_token = request.headers.get("X-Internal-Token") or ""
    if internal_token and presented_internal_token and secrets.compare_digest(presented_internal_token, internal_token):
        project_id = _resolve_project_id(request, internal=True)
        return AuthContext(
            auth_type="internal",
            project_id=project_id,
            scopes={"*"},
            subject="internal-service",
            is_admin=True,
        )

    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            registry = _api_key_registry()
        except Exception as exc:
            raise ServiceError(
                code="AUTH_CONFIG_INVALID",
                message=f"api key registry is invalid: {exc}",
                service=_service_name(request),
                state="auth",
                status_code=500,
            )
        api_key = auth_header[7:].strip()
        definition = registry.get(api_key)
        if not definition or not definition.enabled:
            raise _unauthorized(request, "AUTH_INVALID", "invalid api key")
        project_id = _resolve_project_id(request, definition)
        return AuthContext(
            auth_type="project",
            project_id=project_id,
            scopes=set(definition.scopes),
            subject=definition.name or definition.project_id,
            is_admin=definition.is_admin,
        )

    return AuthContext()


def attach_request_context(request: Request, trace_id: str):
    auth_context = resolve_auth_context(request)
    request.state.trace_id = trace_id
    request.state.auth_context = auth_context
    request.state.project_id = auth_context.project_id
    set_current_trace_id(trace_id)


def get_auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        context = resolve_auth_context(request)
        request.state.auth_context = context
        request.state.project_id = context.project_id
    return context


def has_cross_project_access(request: Request) -> bool:
    context = get_auth_context(request)
    return context.is_internal or context.is_admin


def assert_project_access(
    *,
    service: str,
    resource_project_id: str | None,
    request_project_id: str | None,
    allow_cross_project: bool,
    resource_name: str,
    state: str,
):
    if allow_cross_project:
        return
    if not resource_project_id or not request_project_id or resource_project_id != request_project_id:
        raise ServiceError(
            code="RESOURCE_FORBIDDEN",
            message=f"{resource_name} does not belong to current project",
            service=service,
            state=state,
            status_code=403,
        )


def require_access(
    scope: str | None = None,
    *,
    allow_internal: bool = True,
    require_project: bool = True,
    admin_only: bool = False,
) -> Callable[[Request], AuthContext]:
    def dependency(request: Request) -> AuthContext:
        context = get_auth_context(request)
        if context.is_internal:
            if allow_internal:
                return context
            raise _unauthorized(request, "FORBIDDEN", "internal access is not allowed", 403)

        if not context.is_project:
            raise _unauthorized(request, "AUTH_REQUIRED", "missing bearer api key")

        if require_project and not context.project_id and not context.is_admin:
            raise _unauthorized(request, "PROJECT_CONTEXT_MISSING", "project id is required", 403)

        if admin_only and not context.is_admin:
            raise _unauthorized(request, "FORBIDDEN", "admin scope required", 403)

        if scope and not context.has_scope(scope):
            raise _unauthorized(request, "FORBIDDEN", f"missing scope: {scope}", 403)

        return context

    return dependency


def require_internal_or_admin() -> Callable[[Request], AuthContext]:
    return require_access(allow_internal=True, require_project=False, admin_only=True)
