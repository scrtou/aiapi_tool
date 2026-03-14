"""
SmailPro 临时邮箱客户端

文档：
- https://smailpro.com/api

说明：
- 需要在请求头中提供 `X-Api-Key`
- 使用 Sonjj API 的 `temp_email` 接口族
"""

import os
import re
import secrets
import string
from typing import Optional, List, Any

import requests

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


SMAILPRO_BASE_URL = os.getenv("SMAILPRO_BASE_URL", "https://app.sonjj.com")
SMAILPRO_API_KEY = os.getenv("SMAILPRO_API_KEY", "")


class SmailProClient:
    """SmailPro / Sonjj temp_email 客户端"""

    def __init__(self, base_url: str = SMAILPRO_BASE_URL, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or SMAILPRO_API_KEY).strip()
        if not self.api_key:
            raise ValueError("SMAILPRO_API_KEY 未配置")

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

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "X-Api-Key": self.api_key,
        }

    def list_domains(self) -> List[str]:
        log_message("[SmailPro] 获取域名列表")
        resp = self.session.get(
            f"{self.base_url}/v1/temp_email/domains",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        domains = resp.json().get("domains", []) or []
        log_message(f"[SmailPro] 获取域名成功: {domains}")
        return domains

    def create_account(
        self,
        email_prefix: Optional[str] = None,
        domain: Optional[str] = None,
        password: Optional[str] = None,
    ) -> DuckMailAccount:
        if not email_prefix:
            email_prefix = self.generate_email_prefix()
        if not password:
            password = self.generate_password()
        if not domain:
            domains = self.list_domains()
            if not domains:
                raise ValueError("SmailPro 未返回可用域名")
            domain = domains[0]

        address = f"{email_prefix}@{domain}"
        log_message(f"[SmailPro] 创建邮箱: {address}")

        resp = self.session.get(
            f"{self.base_url}/v1/temp_email/create",
            params={"email": address},
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        self.account = DuckMailAccount(
            address=data.get("email", address),
            password=password,
            account_id=str(data.get("expired_at") or data.get("action") or address),
            token=self.api_key,
        )
        return self.account

    def get_token(self, address: Optional[str] = None, password: Optional[str] = None) -> str:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")
        self.account.token = self.api_key
        return self.api_key

    def list_messages(self) -> List[EmailMessage]:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")

        resp = self.session.get(
            f"{self.base_url}/v1/temp_email/inbox",
            params={"email": self.account.address},
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("messages", []) or []

        messages: List[EmailMessage] = []
        for item in items:
            messages.append(
                EmailMessage(
                    id=str(item.get("mid") or ""),
                    subject=item.get("textSubject", ""),
                    from_address=item.get("textFrom", ""),
                    from_name=item.get("textFrom", ""),
                    created_at=item.get("textDate", ""),
                    seen=False,
                )
            )

        messages.sort(key=lambda x: x.created_at, reverse=True)
        log_message(f"[SmailPro] 获取到 {len(messages)} 封邮件")
        return messages

    def get_message(self, message_id: str) -> EmailDetail:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")

        resp = self.session.get(
            f"{self.base_url}/v1/temp_email/message",
            params={"email": self.account.address, "mid": message_id},
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        body = data.get("body", "") or ""

        return EmailDetail(
            id=message_id,
            subject="",
            from_address="",
            text=body,
            html=[body] if body else [],
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
