from __future__ import annotations

from libs.contracts.mail import MailAccount, MailMessageDetail, MailMessageSummary
from services.mail_service.providers.base import MailProvider
from libs.clients.mailcx_client import MailCxClient


class MailCxProvider(MailProvider):
    provider_name = "mailcx"

    def _build_client(self) -> MailCxClient:
        return MailCxClient()

    def create_account(self, *, domain=None, pattern=None, expiry_time_ms=None, options=None) -> MailAccount:
        client = self._build_client()
        prefix = None
        if pattern and "@" in pattern:
            prefix = pattern.split("@", 1)[0]
        account = client.create_account(email_prefix=prefix, domain=domain or "mail.cx")
        token = client.get_token()
        meta = {"token": token, "account_id": account.account_id, "domain": account.address.split('@',1)[1]}
        return MailAccount(
            provider=self.provider_name,
            account_id=str(account.account_id),
            address=account.address,
            password=account.password,
            status="active",
            meta=meta,
        )

    def _attach(self, client: MailCxClient, account: MailAccount):
        client.account = type("Account", (), {})()
        client.account.address = account.address
        client.account.password = account.password
        client.account.account_id = account.account_id
        client.account.token = account.meta.get("token")

    def list_messages(self, account: MailAccount) -> list[MailMessageSummary]:
        client = self._build_client()
        self._attach(client, account)
        messages = client.list_messages()
        return [MailMessageSummary(id=m.id, from_address=m.from_address, from_name=m.from_name, subject=m.subject, received_at=m.created_at, seen=m.seen) for m in messages]

    def get_message(self, account: MailAccount, message_id: str) -> MailMessageDetail:
        client = self._build_client()
        self._attach(client, account)
        detail = client.get_message(message_id)
        return MailMessageDetail(id=detail.id, from_address=detail.from_address, subject=detail.subject, text=detail.text, html=detail.html[0] if detail.html else "", attachments=[])

    def delete_account(self, account: MailAccount) -> None:
        return None


    def list_domains(self) -> list[str]:
        return ["mail.cx"]

    def health_check(self) -> dict[str, object]:
        try:
            self._build_client()
            return {"available": True, "error": None, "domains": self.list_domains()}
        except Exception as exc:
            return {"available": False, "error": str(exc)}
