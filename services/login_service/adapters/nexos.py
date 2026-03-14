from __future__ import annotations

from typing import Any

from libs.clients.nexos_client import NexosAuthClient, flow_message_texts
from libs.contracts.login import LoginCredentials, LoginResult
from libs.contracts.registration import RegistrationIdentityResult, RegistrationSession
from libs.core.exceptions import ServiceError
from services.login_service.adapters.base import LoginAdapter
from services.shared.nexos_drission_flow import NexosDrissionFlow


class NexosLoginAdapter(LoginAdapter):
    site_name = "nexos"

    def _browser_mode_requested(self, strategy: dict | None) -> bool:
        if not isinstance(strategy, dict):
            return False
        mode = str(strategy.get("mode") or strategy.get("login_mode") or "").strip().lower()
        return mode in {"browser", "drission", "drissionpage", "ui", "browser_ui", "headful"}

    def _message_blob(self, payload: dict[str, Any] | None) -> str:
        return " ".join(text.strip() for text in flow_message_texts(payload)).lower()

    def _extract_session_handle(self, client: NexosAuthClient, payload: dict[str, Any] | None) -> str | None:
        token = None
        if isinstance(payload, dict):
            token = payload.get("session_token")
            if not token:
                session = payload.get("session")
                if isinstance(session, dict):
                    token = session.get("session_token") or session.get("token")
            if not token:
                items = payload.get("continue_with") or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    token = item.get("session_token") or item.get("ory_session_token") or item.get("token")
                    if token:
                        break
        cookie_header = client.current_cookie_header()
        handle = NexosAuthClient.encode_session_handle(token, cookie_header=cookie_header or None)
        return handle or None

    def _extract_email(self, whoami_payload: dict[str, Any]) -> str | None:
        identity = whoami_payload.get("identity") or {}
        traits = identity.get("traits") or {}
        email = traits.get("email")
        if isinstance(email, str) and email:
            return email
        addresses = identity.get("verifiable_addresses") or []
        for address in addresses:
            if isinstance(address, dict) and address.get("value"):
                return str(address.get("value"))
        return None

    def _extract_email_verified(self, whoami_payload: dict[str, Any], email: str | None) -> bool | None:
        identity = whoami_payload.get("identity") or {}
        addresses = identity.get("verifiable_addresses") or []
        for address in addresses:
            if not isinstance(address, dict):
                continue
            value = address.get("value")
            if email and value and str(value).lower() != email.lower():
                continue
            if address.get("verified") is True:
                return True
            status = str(address.get("status") or "").lower()
            if status in {"completed", "verified"}:
                return True
        return None

    def _identity_from_whoami(self, whoami_payload: dict[str, Any]) -> tuple[RegistrationIdentityResult, dict[str, Any], dict[str, Any]]:
        identity = whoami_payload.get("identity") or {}
        identity_id = str(identity.get("id") or "") or None
        session_id = str(whoami_payload.get("id") or "") or None
        email = self._extract_email(whoami_payload)
        email_verified = self._extract_email_verified(whoami_payload, email)
        result_identity = RegistrationIdentityResult(
            external_subject=identity_id,
            external_user_id=identity_id,
        )
        flags = {"email_verified": email_verified}
        site_result = {
            "identity_id": identity_id,
            "session_id": session_id,
            "email": email,
            "email_verified": email_verified,
        }
        return result_identity, flags, site_result

    def login(self, credentials: LoginCredentials, proxy=None, strategy=None) -> LoginResult:
        if self._browser_mode_requested(strategy):
            logs: list[str] = []
            browser_result = NexosDrissionFlow(proxy=proxy).login(credentials, strategy, logs)
            whoami_payload = browser_result.get("whoami_payload") or {}
            identity, flags, site_result = self._identity_from_whoami(whoami_payload)
            flags = {**flags, "browser_login": True}
            site_result = {
                **site_result,
                "browser_login": True,
                "browser_meta": browser_result.get("browser_meta") or {},
                "chat_id": browser_result.get("chat_id"),
                "current_url": browser_result.get("current_url"),
            }
            return LoginResult(
                site=self.site_name,
                account={"email": credentials.email},
                session=RegistrationSession(
                    access_token=str(browser_result.get("session_handle") or ""),
                    refresh_token=None,
                    cookies=browser_result.get("cookies") or [],
                    expires_at=None,
                ),
                identity=identity,
                flags=flags,
                meta={"login_method": "drission_browser", "steps": logs[-80:]},
                site_result=site_result,
            )

        client = NexosAuthClient(proxy=proxy)
        flow = client.create_login_flow()
        status_code, payload = client.submit_login_password(flow, email=credentials.email, password=credentials.password)
        if status_code == 400 and "invalid" in self._message_blob(payload):
            raise ServiceError(
                code="LOGIN_INVALID_CREDENTIALS",
                message="invalid credentials",
                service="login-service",
                state="login",
                retryable=False,
                status_code=401,
            )
        if status_code not in {200, 201}:
            raise ServiceError(
                code="LOGIN_API_FAILED",
                message=" ".join(flow_message_texts(payload)) or str(payload),
                service="login-service",
                state="login",
                retryable=status_code >= 500,
                details={"status_code": status_code},
                status_code=503 if status_code >= 500 else 422,
            )
        session_handle = self._extract_session_handle(client, payload)
        if not session_handle:
            raise ServiceError(
                code="LOGIN_TOKEN_NOT_RETURNED",
                message="nexos login did not return a reusable session",
                service="login-service",
                state="login",
                retryable=False,
                status_code=502,
            )
        valid, whoami_payload = client.whoami(session_handle)
        if not valid or not whoami_payload:
            raise ServiceError(
                code="LOGIN_VERIFY_FAILED",
                message="nexos login session could not be verified",
                service="login-service",
                state="login_whoami",
                retryable=False,
                status_code=502,
            )
        identity, flags, site_result = self._identity_from_whoami(whoami_payload)
        return LoginResult(
            site=self.site_name,
            account={"email": credentials.email},
            session=RegistrationSession(
                access_token=session_handle,
                refresh_token=None,
                cookies=client.current_cookies(),
                expires_at=None,
            ),
            identity=identity,
            flags=flags,
            meta={"login_method": "ory_native_api"},
            site_result=site_result,
        )

    def verify_session(self, token: str) -> tuple[bool, RegistrationIdentityResult, dict]:
        try:
            client = NexosAuthClient()
            valid, whoami_payload = client.whoami(token)
            if not valid or not whoami_payload:
                return False, RegistrationIdentityResult(), {}
            identity, _, site_result = self._identity_from_whoami(whoami_payload)
            return True, identity, site_result
        except Exception:
            return False, RegistrationIdentityResult(), {}
