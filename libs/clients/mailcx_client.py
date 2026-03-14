"""
MailCx 临时邮箱客户端

提供与 DuckMailClient 兼容的最小接口：
- create_account
- get_token
- list_messages
- get_message
- is_verification_email
"""

import re
import secrets
import string
import requests
from typing import Optional, List, Any

try:
    from libs.clients.duckmail_client import (
        DuckMailAccount,
        EmailMessage,
        EmailDetail,
        DEFAULT_SUBJECT_PATTERNS,
        VERIFICATION_SENDERS,
        log_message,
    )
except ImportError:
    from libs.clients.duckmail_client import (
        DuckMailAccount,
        EmailMessage,
        EmailDetail,
        DEFAULT_SUBJECT_PATTERNS,
        VERIFICATION_SENDERS,
        log_message,
    )


MAILCX_BASE_URL = "https://api.mail.cx/api/v1"
DEFAULT_DOMAIN = "mail.cx"


class MailCxClient:
    """MailCx API 客户端"""

    def __init__(self, base_url: str = MAILCX_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.account: Optional[DuckMailAccount] = None

    @staticmethod
    def generate_email_prefix(length: int = 10) -> str:
        chars = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def generate_password(length: int = 16) -> str:
        chars = string.ascii_letters + string.digits + "!@#$%"
        return ''.join(secrets.choice(chars) for _ in range(length))

    def _headers(self, with_auth: bool = True) -> dict:
        headers = {
            "Accept": "application/json",
        }
        if with_auth and self.account and self.account.token:
            headers["Authorization"] = f"Bearer {self.account.token}"
        return headers

    def _mailbox_name(self) -> str:
        if not self.account or not self.account.address:
            raise ValueError("未创建账户")
        return self.account.address.split("@", 1)[0]

    def create_account(
        self,
        email_prefix: Optional[str] = None,
        domain: str = DEFAULT_DOMAIN,
        password: Optional[str] = None,
    ) -> DuckMailAccount:
        if not email_prefix:
            email_prefix = self.generate_email_prefix()
        if not password:
            password = self.generate_password()

        address = f"{email_prefix}@{domain}"
        self.account = DuckMailAccount(
            address=address,
            password=password,
            account_id=email_prefix,
        )
        log_message(f"[MailCx] 使用邮箱地址: {address}")
        return self.account

    def get_token(self, address: Optional[str] = None, password: Optional[str] = None) -> str:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")

        log_message(f"[MailCx] 获取 token: {self.account.address}")

        resp = self.session.post(
            f"{self.base_url}/auth/authorize_token",
            json={},
            headers=self._headers(with_auth=False),
            timeout=30,
        )
        resp.raise_for_status()

        token = resp.json()
        if not isinstance(token, str):
            token = str(token)

        self.account.token = token
        log_message(f"[MailCx] 获取 token 成功: {token[:20]}...")
        return token

    def _extract_messages(self, payload: Any) -> List[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("hydra:member", "messages", "items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _parse_from_address(self, raw_from: Any) -> tuple[str, str]:
        if isinstance(raw_from, dict):
            return raw_from.get("address", "") or raw_from.get("email", ""), raw_from.get("name", "")
        if isinstance(raw_from, list) and raw_from:
            return self._parse_from_address(raw_from[0])
        if isinstance(raw_from, str):
            match = re.search(r'<([^>]+)>', raw_from)
            if match:
                return match.group(1), raw_from.replace(match.group(0), "").strip().strip('"')
            return raw_from, ""
        return "", ""

    def list_messages(self) -> List[EmailMessage]:
        if not self.account or not self.account.token:
            raise ValueError("未获取 token，请先调用 get_token()")

        mailbox_name = self._mailbox_name()
        log_message(f"[MailCx] 获取邮件列表: {mailbox_name}")

        resp = self.session.get(
            f"{self.base_url}/mailbox/{mailbox_name}",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()

        items = self._extract_messages(resp.json())
        messages: List[EmailMessage] = []
        for item in items:
            from_address, from_name = self._parse_from_address(item.get("from"))
            messages.append(
                EmailMessage(
                    id=str(item.get("id") or item.get("_id") or item.get("uid") or ""),
                    subject=item.get("subject", "") or item.get("title", ""),
                    from_address=from_address,
                    from_name=from_name,
                    created_at=str(item.get("createdAt") or item.get("created_at") or item.get("date") or ""),
                    seen=bool(item.get("seen", False)),
                )
            )

        messages.sort(key=lambda x: x.created_at, reverse=True)
        log_message(f"[MailCx] 获取到 {len(messages)} 封邮件")
        return messages

    def get_message(self, message_id: str) -> EmailDetail:
        if not self.account or not self.account.token:
            raise ValueError("未获取 token，请先调用 get_token()")

        mailbox_name = self._mailbox_name()
        log_message(f"[MailCx] 获取邮件详情: {message_id}")

        resp = self.session.get(
            f"{self.base_url}/mailbox/{mailbox_name}/{message_id}",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        from_address, _ = self._parse_from_address(data.get("from"))
        html_content = data.get("html") or data.get("htmlBody") or data.get("html_body") or []
        if isinstance(html_content, str):
            html_content = [html_content]
        elif not isinstance(html_content, list):
            html_content = []

        text_content = data.get("text") or data.get("body") or data.get("intro") or ""
        if not text_content and not html_content:
            source_resp = self.session.get(
                f"{self.base_url}/mailbox/{mailbox_name}/{message_id}/source",
                headers=self._headers(),
                timeout=30,
            )
            if source_resp.ok:
                text_content = source_resp.text

        return EmailDetail(
            id=str(data.get("id") or message_id),
            subject=data.get("subject", "") or data.get("title", ""),
            from_address=from_address,
            text=text_content or "",
            html=html_content,
        )

    def is_verification_email(
        self,
        message: EmailMessage,
        subject_patterns: Optional[List[str]] = None,
        sender_whitelist: Optional[List[str]] = None,
    ) -> bool:
        subject_patterns = subject_patterns or DEFAULT_SUBJECT_PATTERNS
        sender_whitelist = sender_whitelist or VERIFICATION_SENDERS

        subject = (message.subject or "").lower()
        from_address = (message.from_address or "").lower()

        if sender_whitelist and any(sender.lower() in from_address for sender in sender_whitelist):
            return True

        return any(re.search(pattern, subject, re.IGNORECASE) for pattern in subject_patterns)
