from __future__ import annotations

import json
from typing import Any

import requests

from libs.contracts.proxy import ProxyLease
from libs.core.config import env_int, env_str
from libs.core.exceptions import ServiceError


NEXOS_BASE_URL = env_str("NEXOS_BASE_URL", "https://workspace.nexos.ai") or "https://workspace.nexos.ai"
NEXOS_ORY_BASE_URL = env_str("NEXOS_ORY_BASE_URL", f"{NEXOS_BASE_URL.rstrip('/')}/oryBridge/.ory") or f"{NEXOS_BASE_URL.rstrip('/')}/oryBridge/.ory"
NEXOS_HTTP_TIMEOUT_SECONDS = env_int("NEXOS_HTTP_TIMEOUT_SECONDS", 60)
NEXOS_USER_AGENT = env_str(
    "NEXOS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
) or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def flow_messages(flow_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(flow_payload, dict):
        return []
    ui = flow_payload.get("ui")
    if not isinstance(ui, dict):
        return []
    messages = ui.get("messages")
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


def flow_message_texts(flow_payload: dict[str, Any] | None) -> list[str]:
    return [str(item.get("text") or "") for item in flow_messages(flow_payload)]


def is_flow_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("ui"), dict) and isinstance(payload.get("id"), str)


class NexosAuthClient:
    def __init__(self, proxy: ProxyLease | None = None, session: requests.Session | None = None):
        self.proxy = proxy
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": NEXOS_USER_AGENT,
            }
        )

    def _proxy_config(self) -> dict[str, str] | None:
        if not self.proxy:
            return None
        credentials = ""
        if self.proxy.username:
            credentials = self.proxy.username
            if self.proxy.password:
                credentials = f"{credentials}:{self.proxy.password}"
            credentials = f"{credentials}@"
        scheme = self.proxy.scheme or "http"
        proxy_url = f"{scheme}://{credentials}{self.proxy.host}:{self.proxy.port}"
        return {"http": proxy_url, "https": proxy_url}

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", NEXOS_HTTP_TIMEOUT_SECONDS)
        proxies = self._proxy_config()
        if proxies:
            kwargs.setdefault("proxies", proxies)
        try:
            return self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise ServiceError(
                code="NEXOS_HTTP_REQUEST_FAILED",
                message=str(exc),
                service="nexos-client",
                state="http_request",
                retryable=True,
                status_code=503,
            ) from exc

    def _json(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ServiceError(
                code="NEXOS_INVALID_RESPONSE",
                message=response.text[:300] or str(exc),
                service="nexos-client",
                state="decode_json",
                retryable=response.status_code >= 500,
                details={"status_code": response.status_code},
                status_code=502,
            ) from exc
        if not isinstance(payload, dict):
            raise ServiceError(
                code="NEXOS_INVALID_RESPONSE",
                message="response payload is not an object",
                service="nexos-client",
                state="decode_json",
                retryable=response.status_code >= 500,
                details={"status_code": response.status_code},
                status_code=502,
            )
        return payload

    def create_registration_flow(self) -> dict[str, Any]:
        response = self._request("GET", f"{NEXOS_ORY_BASE_URL}/self-service/registration/api")
        if response.status_code != 200:
            raise ServiceError(
                code="NEXOS_REGISTRATION_FLOW_FAILED",
                message=response.text[:300],
                service="nexos-client",
                state="create_registration_flow",
                retryable=response.status_code >= 500,
                details={"status_code": response.status_code},
                status_code=503 if response.status_code >= 500 else 422,
            )
        return self._json(response)

    def submit_registration_profile(self, flow: dict[str, Any], *, email: str, first_name: str, last_name: str) -> tuple[int, dict[str, Any]]:
        response = self._request(
            "POST",
            flow["ui"]["action"],
            json={
                "method": "profile",
                "traits": {
                    "email": email,
                    "name": {
                        "first": first_name,
                        "last": last_name,
                    },
                },
            },
        )
        return response.status_code, self._json(response)

    def submit_registration_password(
        self,
        flow: dict[str, Any],
        *,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        turnstile_token: str,
    ) -> tuple[int, dict[str, Any]]:
        response = self._request(
            "POST",
            flow["ui"]["action"],
            json={
                "method": "password",
                "password": password,
                "traits": {
                    "email": email,
                    "name": {
                        "first": first_name,
                        "last": last_name,
                    },
                },
                "transient_payload": {
                    "turnstile_token": turnstile_token,
                },
            },
        )
        return response.status_code, self._json(response)

    def create_verification_flow(self) -> dict[str, Any]:
        response = self._request("GET", f"{NEXOS_ORY_BASE_URL}/self-service/verification/api")
        if response.status_code != 200:
            raise ServiceError(
                code="NEXOS_VERIFICATION_FLOW_FAILED",
                message=response.text[:300],
                service="nexos-client",
                state="create_verification_flow",
                retryable=response.status_code >= 500,
                details={"status_code": response.status_code},
                status_code=503 if response.status_code >= 500 else 422,
            )
        return self._json(response)

    def send_verification_code(self, flow: dict[str, Any], *, email: str) -> tuple[int, dict[str, Any]]:
        response = self._request(
            "POST",
            flow["ui"]["action"],
            json={
                "method": "code",
                "email": email,
            },
        )
        return response.status_code, self._json(response)

    def verify_code(self, flow: dict[str, Any], *, code: str) -> tuple[int, dict[str, Any]]:
        response = self._request(
            "POST",
            flow["ui"]["action"],
            json={
                "method": "code",
                "code": code,
            },
        )
        return response.status_code, self._json(response)

    def create_login_flow(self) -> dict[str, Any]:
        response = self._request("GET", f"{NEXOS_ORY_BASE_URL}/self-service/login/api")
        if response.status_code != 200:
            raise ServiceError(
                code="NEXOS_LOGIN_FLOW_FAILED",
                message=response.text[:300],
                service="nexos-client",
                state="create_login_flow",
                retryable=response.status_code >= 500,
                details={"status_code": response.status_code},
                status_code=503 if response.status_code >= 500 else 422,
            )
        return self._json(response)

    def submit_login_password(self, flow: dict[str, Any], *, email: str, password: str) -> tuple[int, dict[str, Any]]:
        response = self._request(
            "POST",
            flow["ui"]["action"],
            json={
                "method": "password",
                "identifier": email,
                "password": password,
            },
        )
        return response.status_code, self._json(response)

    def whoami(self, session_handle: str) -> tuple[bool, dict[str, Any] | None]:
        token, cookie_header = self.decode_session_handle(session_handle)
        headers = {}
        if token:
            headers["X-Session-Token"] = token
        if cookie_header:
            headers["Cookie"] = cookie_header
        response = self._request("GET", f"{NEXOS_ORY_BASE_URL}/sessions/whoami", headers=headers)
        if response.status_code == 401:
            return False, None
        if response.status_code != 200:
            raise ServiceError(
                code="NEXOS_WHOAMI_FAILED",
                message=response.text[:300],
                service="nexos-client",
                state="whoami",
                retryable=response.status_code >= 500,
                details={"status_code": response.status_code},
                status_code=503 if response.status_code >= 500 else 422,
            )
        return True, self._json(response)

    def current_cookies(self) -> list[dict[str, Any]]:
        output = []
        for cookie in self.session.cookies:
            output.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": bool(cookie.secure),
                    "expires": cookie.expires,
                }
            )
        return output

    def current_cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.session.cookies)

    @staticmethod
    def encode_session_handle(session_token: str | None = None, *, cookie_header: str | None = None) -> str:
        if session_token:
            return session_token
        if cookie_header:
            return json.dumps({"cookie": cookie_header}, separators=(",", ":"))
        return ""

    @staticmethod
    def decode_session_handle(session_handle: str | None) -> tuple[str | None, str | None]:
        if not session_handle:
            return None, None
        try:
            payload = json.loads(session_handle)
        except Exception:
            return session_handle, None
        if isinstance(payload, dict):
            token = payload.get("session_token") or payload.get("token")
            cookie_header = payload.get("cookie") or payload.get("cookie_header")
            return token, cookie_header
        return session_handle, None

