from __future__ import annotations

from libs.contracts.mail import MailAccount, MailMessageDetail, MailMessageSummary
from services.mail_service.providers.base import MailProvider
from libs.clients.moemail_client import MoeMailClient


class MoeMailProvider(MailProvider):
    provider_name = "moemail"

    def _build_client(self) -> MoeMailClient:
        return MoeMailClient()

    def create_account(self, *, domain=None, pattern=None, expiry_time_ms=None, options=None) -> MailAccount:
        client = self._build_client()
        account = client.create_account(domain=domain)
        meta = {
            "domain": account.address.split("@", 1)[1],
        }
        return MailAccount(
            provider=self.provider_name,
            account_id=str(account.account_id),
            address=account.address,
            password=account.password,
            expires_at=None,
            meta=meta,
        )

    def list_messages(self, account: MailAccount) -> list[MailMessageSummary]:
        client = self._build_client()
        client.account = type("Account", (), {})()
        client.account.address = account.address
        client.account.account_id = account.account_id
        messages = client.list_messages()
        return [
            MailMessageSummary(
                id=m.id,
                from_address=m.from_address,
                from_name=m.from_name,
                subject=m.subject,
                received_at=m.created_at,
                seen=m.seen,
            )
            for m in messages
        ]

    def get_message(self, account: MailAccount, message_id: str) -> MailMessageDetail:
        client = self._build_client()
        client.account = type("Account", (), {})()
        client.account.address = account.address
        client.account.account_id = account.account_id
        detail = client.get_message(message_id)
        return MailMessageDetail(
            id=detail.id,
            from_address=detail.from_address,
            subject=detail.subject,
            text=detail.text,
            html=detail.html[0] if detail.html else "",
            attachments=[],
        )

    def delete_account(self, account: MailAccount) -> None:
        client = self._build_client()
        client.account = type("Account", (), {})()
        client.account.address = account.address
        client.account.account_id = account.account_id
        client.delete_account()


    def list_domains(self) -> list[str]:
        client = self._build_client()
        return client.list_domains()

    def health_check(self) -> dict[str, object]:
        try:
            domains = self.list_domains()
            return {"available": True, "error": None, "domains": domains}
        except Exception as exc:
            return {"available": False, "error": str(exc)}
