from __future__ import annotations

from libs.contracts.mail import MailAccount, MailMessageDetail, MailMessageSummary
from services.mail_service.providers.base import MailProvider
from libs.clients.smailpro_web_client import SmailProWebClient


class SmailProWebProvider(MailProvider):
    provider_name = "smailpro_web"

    def _build_client(self) -> SmailProWebClient:
        return SmailProWebClient()

    def create_account(self, *, domain=None, pattern=None, expiry_time_ms=None, options=None) -> MailAccount:
        client = self._build_client()
        account = client.create_account(domain=domain, pattern=pattern)
        meta = dict(client.account_meta or {})
        return MailAccount(provider=self.provider_name, account_id=str(account.account_id), address=account.address, password=account.password, meta=meta)

    def list_messages(self, account: MailAccount) -> list[MailMessageSummary]:
        client = self._build_client()
        client.account = type("Account", (), {})()
        client.account.address = account.address
        client.account.password = account.password
        client.account.account_id = account.account_id
        client.account_meta = dict(account.meta)
        messages = client.list_messages()
        try:
            client.close()
        except Exception:
            pass
        return [MailMessageSummary(id=m.id, from_address=m.from_address, from_name=m.from_name, subject=m.subject, received_at=m.created_at, seen=m.seen) for m in messages]

    def get_message(self, account: MailAccount, message_id: str) -> MailMessageDetail:
        client = self._build_client()
        client.account = type("Account", (), {})()
        client.account.address = account.address
        client.account.password = account.password
        client.account.account_id = account.account_id
        client.account_meta = dict(account.meta)
        detail = client.get_message(message_id)
        try:
            client.close()
        except Exception:
            pass
        return MailMessageDetail(id=detail.id, from_address=detail.from_address, subject=detail.subject, text=detail.text, html=detail.html[0] if detail.html else "", attachments=[])

    def delete_account(self, account: MailAccount) -> None:
        return None


    def list_domains(self) -> list[str]:
        return []

    def health_check(self) -> dict[str, object]:
        try:
            self._build_client()
            return {"available": True, "error": None, "domains": self.list_domains()}
        except Exception as exc:
            return {"available": False, "error": str(exc)}
