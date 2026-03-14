from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentityResult, RegistrationSession


class LoginCredentials(BaseModel):
    email: str
    password: str


class LoginResult(BaseModel):
    project_id: Optional[str] = None
    site: str
    account: dict[str, Any]
    session: RegistrationSession
    identity: RegistrationIdentityResult
    flags: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    site_result: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    site: str
    credentials: LoginCredentials
    proxy: Optional[ProxyLease] = None
    strategy: dict[str, Any] = Field(default_factory=dict)


class LoginData(BaseModel):
    result: LoginResult


class LoginResultsData(BaseModel):
    results: list[LoginResult] = Field(default_factory=list)
    total: int = 0


class VerifySessionRequest(BaseModel):
    site: str
    token: str


class VerifySessionData(BaseModel):
    valid: bool
    identity: Optional[RegistrationIdentityResult] = None
    site_result: dict[str, Any] = Field(default_factory=dict)
