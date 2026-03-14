"""
MoeMail 临时邮箱客户端

兼容自动注册流程所需的最小接口：
- create_account
- get_token
- list_messages
- get_message
- is_verification_email
"""

import os
import re
import secrets
import string
from typing import Optional, List

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


MOEMAIL_BASE_URL = os.getenv("MOEMAIL_BASE_URL", "https://moemail-bqw.pages.dev")
MOEMAIL_API_KEY = os.getenv("MOEMAIL_API_KEY", "mk_n7D3gcpyxg1lnTMjnh2ExLp3YR586chi")
MOEMAIL_EXPIRY_TIME = int(os.getenv("MOEMAIL_EXPIRY_TIME", "3600000"))


class MoeMailClient:
    """MoeMail API 客户端"""

    def __init__(self, base_url: str = MOEMAIL_BASE_URL, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or MOEMAIL_API_KEY).strip()
        if not self.api_key:
            raise ValueError("MOEMAIL_API_KEY 未配置")

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
            "X-API-Key": self.api_key,
        }

    def get_config(self) -> dict:
        log_message("[MoeMail] 获取系统配置")
        response = self.session.get(
            f"{self.base_url}/api/config",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_domains(self) -> List[str]:
        config = self.get_config()
        domains = [d.strip() for d in (config.get("emailDomains") or "").split(",") if d.strip()]
        log_message(f"[MoeMail] 可用域名: {domains}")
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
                raise ValueError("MoeMail 未返回可用域名")
            domain = domains[0]

        payload = {
            "name": email_prefix,
            "expiryTime": MOEMAIL_EXPIRY_TIME,
            "domain": domain,
        }
        log_message(f"[MoeMail] 创建邮箱: {email_prefix}@{domain}")

        response = self.session.post(
            f"{self.base_url}/api/emails/generate",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        self.account = DuckMailAccount(
            address=data.get("email", f"{email_prefix}@{domain}"),
            password=password,
            account_id=data.get("id"),
            token=self.api_key,
        )
        log_message(f"[MoeMail] 创建成功: {self.account.address}, id={self.account.account_id}")
        return self.account

    def get_token(self, address: Optional[str] = None, password: Optional[str] = None) -> str:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")
        return self.api_key

    def list_messages(self) -> List[EmailMessage]:
        if not self.account or not self.account.account_id:
            raise ValueError("未创建邮箱账户")

        response = self.session.get(
            f"{self.base_url}/api/emails/{self.account.account_id}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        messages: List[EmailMessage] = []
        for item in data.get("messages", []) or []:
            messages.append(
                EmailMessage(
                    id=str(item.get("id") or ""),
                    subject=item.get("subject", ""),
                    from_address=item.get("from_address", ""),
                    from_name=item.get("from_address", ""),
                    created_at=str(item.get("received_at") or ""),
                    seen=False,
                )
            )

        messages.sort(key=lambda x: x.created_at, reverse=True)
        log_message(f"[MoeMail] 获取到 {len(messages)} 封邮件")
        return messages

    def get_message(self, message_id: str) -> EmailDetail:
        if not self.account or not self.account.account_id:
            raise ValueError("未创建邮箱账户")

        response = self.session.get(
            f"{self.base_url}/api/emails/{self.account.account_id}/{message_id}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("message"), dict):
            data = data["message"]

        html_content = data.get("html") or ""
        html_list = [html_content] if html_content else []

        return EmailDetail(
            id=str(data.get("id") or message_id),
            subject=data.get("subject", ""),
            from_address=data.get("from_address", ""),
            text=data.get("content", "") or data.get("text", "") or "",
            html=html_list,
        )

    def delete_account(self):
        if not self.account or not self.account.account_id:
            return
        try:
            self.session.delete(
                f"{self.base_url}/api/emails/{self.account.account_id}",
                headers=self._headers(),
                timeout=30,
            )
        except Exception:
            pass

    def close(self):
        self.delete_account()

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
