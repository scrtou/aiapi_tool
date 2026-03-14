from __future__ import annotations

import os
import re
from dataclasses import dataclass

from libs.contracts.mail import MailAccount
from libs.core.config import env_int, env_str
from libs.core.exceptions import ServiceError
from libs.core.http import ServiceHttpClient


DEFAULT_SUBJECT_PATTERNS = [
    r"Welcome to chayns",
    r"verify",
    r"activate",
    r"confirm",
    r"Willkommen",
    r"bestätigen",
]

VERIFICATION_SENDERS = [
    "noreply@chayns.de",
    "no-reply@chayns.de",
]


@dataclass
class MailboxMessage:
    id: str
    subject: str
    from_address: str
    from_name: str
    created_at: str
    seen: bool = False


@dataclass
class MailboxDetail:
    id: str
    subject: str
    from_address: str
    text: str
    html: list[str]


class MailServiceMailboxClient:
    def __init__(self, account: MailAccount):
        self.account = account
        self.client = ServiceHttpClient(
            service_name="mail-service",
            base_url=env_str("MAIL_SERVICE_URL", "http://localhost:8001"),
            internal_token=env_str("INTERNAL_SERVICE_TOKEN"),
            timeout=env_int("REGISTRATION_MAIL_HTTP_TIMEOUT_SECONDS", 120),
        )

    def list_messages(self):
        payload = self.client.get(
            f"/api/v1/mail/accounts/{self.account.account_id}/messages",
            trace_id=f"mailacct_{self.account.account_id}",
            project_id=self.account.project_id,
        )
        messages = payload["data"]["messages"]
        return [
            MailboxMessage(
                id=item["id"],
                subject=item.get("subject", ""),
                from_address=item.get("from_address", ""),
                from_name=item.get("from_name", ""),
                created_at=item.get("received_at", ""),
                seen=item.get("seen", False),
            )
            for item in messages
        ]

    def get_message(self, message_id: str):
        payload = self.client.get(
            f"/api/v1/mail/accounts/{self.account.account_id}/messages/{message_id}",
            trace_id=f"mailmsg_{message_id}",
            project_id=self.account.project_id,
        )
        data = payload["data"]["message"]
        html = data.get("html") or ""
        html_list = [html] if html else []
        return MailboxDetail(
            id=data["id"],
            subject=data.get("subject", ""),
            from_address=data.get("from_address", ""),
            text=data.get("text", ""),
            html=html_list,
        )

    def is_verification_email(self, message: MailboxMessage, subject_patterns=None, sender_whitelist=None) -> bool:
        subject_patterns = subject_patterns or DEFAULT_SUBJECT_PATTERNS
        sender_whitelist = sender_whitelist or VERIFICATION_SENDERS
        subject = (message.subject or "").lower()
        from_address = (message.from_address or "").lower()
        if sender_whitelist and any(sender.lower() in from_address for sender in sender_whitelist):
            return True
        return any(re.search(pattern, subject, re.IGNORECASE) for pattern in subject_patterns)
