from __future__ import annotations

from abc import ABC, abstractmethod

from libs.contracts.login import LoginCredentials, LoginResult
from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentityResult


class LoginAdapter(ABC):
    site_name: str

    @abstractmethod
    def login(self, credentials: LoginCredentials, proxy: ProxyLease | None = None, strategy: dict | None = None) -> LoginResult:
        raise NotImplementedError

    @abstractmethod
    def verify_session(self, token: str) -> tuple[bool, RegistrationIdentityResult, dict]:
        raise NotImplementedError
