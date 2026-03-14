from __future__ import annotations

from dataclasses import asdict

from libs.contracts.mail import MailAccount, MailMessageDetail, MailMessageSummary
from services.mail_service.providers.base import MailProvider
from libs.clients.duckmail_client import DuckMailAccount, DuckMailClient


class DuckMailProvider(MailProvider):
    provider_name = "duckmail"

    def _build_client(self) -> DuckMailClient:
        return DuckMailClient()

    def create_account(self, *, domain=None, pattern=None, expiry_time_ms=None, options=None) -> MailAccount:
        client = self._build_client()
        prefix = None
        if pattern and "@" in pattern:
            prefix = pattern.split("@", 1)[0]
        account = client.create_account(email_prefix=prefix, domain=domain or "duckmail.sbs")
        token = client.get_token()
        meta = {"token": token, "account_id": account.account_id, "domain": account.address.split('@',1)[1]}
        return MailAccount(
            provider=self.provider_name,
            account_id=str(account.account_id),
            address=account.address,
            password=account.password,
            expires_at=None,
            meta=meta,
        )

    def _attach(self, client: DuckMailClient, account: MailAccount):
        client.account = DuckMailAccount(
            address=account.address,
            password=account.password,
            account_id=account.account_id,
            token=account.meta.get("token"),
        )

    def list_messages(self, account: MailAccount) -> list[MailMessageSummary]:
        client = self._build_client()
        self._attach(client, account)
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
        self._attach(client, account)
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
        # DuckMail 当前客户端未实现删除，MVP 先忽略
        return None


    def list_domains(self) -> list[str]:
        client = self._build_client()
        return [item.domain for item in client.list_domains()]

    def health_check(self) -> dict[str, object]:
        try:
            domains = self.list_domains()
            return {"available": True, "error": None, "domains": domains}
        except Exception as exc:
            return {"available": False, "error": str(exc)}
