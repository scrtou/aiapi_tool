from __future__ import annotations

from abc import ABC, abstractmethod

from libs.contracts.mail import MailAccount
from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentity, RegistrationResult


class RegistrationAdapter(ABC):
    site_name: str

    @abstractmethod
    def register(self, identity: RegistrationIdentity, mail_account: MailAccount, proxy: ProxyLease | None = None, strategy: dict | None = None) -> RegistrationResult:
        raise NotImplementedError
