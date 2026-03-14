from __future__ import annotations

from abc import ABC, abstractmethod

from libs.contracts.mail import MailAccount, MailMessageDetail, MailMessageSummary


class MailProvider(ABC):
    provider_name: str

    @abstractmethod
    def create_account(self, *, domain: str | None = None, pattern: str | None = None, expiry_time_ms: int | None = None, options: dict | None = None) -> MailAccount:
        raise NotImplementedError

    @abstractmethod
    def list_messages(self, account: MailAccount) -> list[MailMessageSummary]:
        raise NotImplementedError

    @abstractmethod
    def get_message(self, account: MailAccount, message_id: str) -> MailMessageDetail:
        raise NotImplementedError

    @abstractmethod
    def delete_account(self, account: MailAccount) -> None:
        raise NotImplementedError


    def list_domains(self) -> list[str]:
        return []

    def health_check(self) -> dict[str, object]:
        try:
            self.list_domains()
            return {"available": True, "error": None}
        except Exception as exc:
            return {"available": False, "error": str(exc)}
