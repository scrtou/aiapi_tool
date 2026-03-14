from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from libs.contracts.login import LoginCredentials, LoginResult
from libs.contracts.registration import RegistrationIdentityResult, RegistrationSession
from libs.core.exceptions import ServiceError
from services.login_service.adapters.base import LoginAdapter


AUTH_API_BASE_URL = os.getenv("CHAYNS_AUTH_API_BASE_URL", "https://auth.tobit.com/v2")
AUTH_LOCATION_ID = int(os.getenv("CHAYNS_LOCATION_ID", "153008"))
AUTH_TOKEN_TYPE = int(os.getenv("CHAYNS_LOGIN_TOKEN_TYPE", "1"))
USER_SETTINGS_API_URL = os.getenv(
    "CHAYNS_USER_SETTINGS_API_URL",
    "https://cube.tobit.cloud/ai-proxy/v1/userSettings/personId/{personId}",
)


class ChaynsLoginAdapter(LoginAdapter):
    site_name = "chayns"

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict[str, Any]:
        payload = token.split('.')[1]
        padding = '=' * (-len(payload) % 4)
        import base64 as _b64
        decoded = _b64.urlsafe_b64decode(payload + padding)
        return json.loads(decoded.decode('utf-8'))

    @staticmethod
    def _get_pro_access(token: str, person_id: str) -> bool | None:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        response = requests.get(USER_SETTINGS_API_URL.format(personId=person_id), headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json().get("hasProAccess")
        return None

    def login(self, credentials: LoginCredentials, proxy=None, strategy=None) -> LoginResult:
        basic = base64.b64encode(f"{credentials.email}:{credentials.password}".encode()).decode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {basic}",
        }
        body = {
            "locationId": AUTH_LOCATION_ID,
            "isConfirmation": False,
            "tokenType": AUTH_TOKEN_TYPE,
            "deviceId": str(uuid.uuid4()),
            "debug": 0,
        }
        response = requests.post(f"{AUTH_API_BASE_URL}/token", headers=headers, json=body, timeout=30)
        if response.status_code == 401:
            raise ServiceError(
                code="LOGIN_INVALID_CREDENTIALS",
                message="invalid credentials",
                service="login-service",
                state="api_login",
                status_code=401,
            )
        if response.status_code != 200:
            raise ServiceError(
                code="LOGIN_API_FAILED",
                message=response.text[:300],
                service="login-service",
                state="api_login",
                retryable=True,
                details={"status_code": response.status_code},
                status_code=503 if response.status_code >= 500 else 422,
            )

        payload = response.json()
        token = payload.get("token")
        expires = payload.get("expires")
        if not token:
            raise ServiceError(
                code="LOGIN_TOKEN_NOT_RETURNED",
                message="token missing in login response",
                service="login-service",
                state="api_login",
                status_code=502,
            )
        jwt_payload = self._decode_jwt_payload(token)
        user_id = jwt_payload.get("TobitUserID") or jwt_payload.get("userId") or jwt_payload.get("userid")
        person_id = jwt_payload.get("PersonID") or jwt_payload.get("personId") or jwt_payload.get("sub")
        if not user_id or not person_id:
            raise ServiceError(
                code="LOGIN_PROFILE_EXTRACT_FAILED",
                message="could not extract user identity from token",
                service="login-service",
                state="profile_loaded",
                status_code=502,
            )
        has_pro_access = self._get_pro_access(token, str(person_id))
        session = RegistrationSession(
            access_token=token,
            refresh_token=None,
            cookies=[],
            expires_at=expires,
        )
        identity = RegistrationIdentityResult(
            external_subject=str(person_id),
            external_user_id=str(user_id),
        )
        return LoginResult(
            site=self.site_name,
            account={"email": credentials.email},
            session=session,
            identity=identity,
            flags={"has_pro_access": has_pro_access},
            meta={"login_method": "api"},
            site_result={
                "userid": int(user_id),
                "personid": str(person_id),
                "has_pro_access": has_pro_access,
            },
        )

    def verify_session(self, token: str) -> tuple[bool, RegistrationIdentityResult, dict]:
        try:
            payload = self._decode_jwt_payload(token)
            user_id = payload.get("TobitUserID") or payload.get("userId") or payload.get("userid")
            person_id = payload.get("PersonID") or payload.get("personId") or payload.get("sub")
            if not user_id or not person_id:
                return False, RegistrationIdentityResult(), {}
            identity = RegistrationIdentityResult(
                external_subject=str(person_id),
                external_user_id=str(user_id),
            )
            site_result = {
                "userid": int(user_id),
                "personid": str(person_id),
            }
            return True, identity, site_result
        except Exception:
            return False, RegistrationIdentityResult(), {}
