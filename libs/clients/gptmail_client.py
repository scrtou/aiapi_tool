from __future__ import annotations

import os
import re
from typing import Optional, List

import requests

from libs.clients.duckmail_client import (
    DuckMailAccount,
    EmailDetail,
    EmailMessage,
    DEFAULT_SUBJECT_PATTERNS,
    VERIFICATION_SENDERS,
    log_message,
)


GPTMAIL_BASE_URL = os.getenv("GPTMAIL_BASE_URL", "https://mail.chatgpt.org.uk")
GPTMAIL_API_KEY = os.getenv("GPTMAIL_API_KEY", "")


class GPTMailClient:
    """GPTMail API 客户端"""

    def __init__(self, base_url: str = GPTMAIL_BASE_URL, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or GPTMAIL_API_KEY).strip()
        if not self.api_key:
            raise ValueError("GPTMAIL_API_KEY 未配置")
        self.session = requests.Session()
        self.account: Optional[DuckMailAccount] = None

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }

    def create_account(self, email_prefix: Optional[str] = None, domain: Optional[str] = None, password: Optional[str] = None) -> DuckMailAccount:
        if email_prefix or domain:
            payload = {}
            if email_prefix:
                payload["prefix"] = email_prefix
            if domain:
                payload["domain"] = domain
            response = self.session.post(
                f"{self.base_url}/api/generate-email",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
        else:
            response = self.session.get(
                f"{self.base_url}/api/generate-email",
                headers=self._headers(),
                timeout=30,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise ValueError(f"GPTMail 创建邮箱失败: {data}")
        email = data.get("data", {}).get("email")
        if not email:
            raise ValueError(f"GPTMail 响应缺少 email: {data}")
        self.account = DuckMailAccount(address=email, password=password or "", account_id=email, token=self.api_key)
        log_message(f"[GPTMail] 创建成功: {email}")
        return self.account

    def get_token(self, address: Optional[str] = None, password: Optional[str] = None) -> str:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")
        return self.api_key

    def list_messages(self) -> List[EmailMessage]:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")
        response = self.session.get(
            f"{self.base_url}/api/emails",
            headers=self._headers(),
            params={"email": self.account.address},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise ValueError(f"GPTMail 获取邮件列表失败: {data}")
        payload = data.get("data", {}) or {}
        items = payload.get("emails", []) or []
        messages: List[EmailMessage] = []
        for item in items:
            messages.append(
                EmailMessage(
                    id=str(item.get("id") or item.get("message_id") or item.get("messageId") or ""),
                    subject=item.get("subject", "") or item.get("title", ""),
                    from_address=item.get("from") or item.get("from_address") or item.get("fromAddress") or "",
                    from_name=item.get("from_name") or item.get("fromName") or item.get("from") or "",
                    created_at=str(item.get("date") or item.get("created_at") or item.get("createdAt") or item.get("received_at") or item.get("receivedAt") or ""),
                    seen=bool(item.get("seen", False)),
                )
            )
        messages.sort(key=lambda x: x.created_at, reverse=True)
        log_message(f"[GPTMail] 获取到 {len(messages)} 封邮件")
        return messages

    def get_message(self, message_id: str) -> EmailDetail:
        response = self.session.get(
            f"{self.base_url}/api/email/{message_id}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise ValueError(f"GPTMail 获取邮件详情失败: {data}")
        payload = data.get("data", {}) or {}
        html_content = payload.get("html") or payload.get("html_content") or ""
        html_list = [html_content] if html_content else []
        return EmailDetail(
            id=str(payload.get("id") or message_id),
            subject=payload.get("subject", "") or payload.get("title", ""),
            from_address=payload.get("from") or payload.get("from_address") or payload.get("fromAddress") or "",
            text=payload.get("text") or payload.get("content") or payload.get("body") or "",
            html=html_list,
        )

    def clear_mailbox(self):
        if not self.account:
            return
        try:
            self.session.delete(
                f"{self.base_url}/api/emails/clear",
                headers=self._headers(),
                params={"email": self.account.address},
                timeout=30,
            )
        except Exception:
            pass

    def close(self):
        self.clear_mailbox()

    def is_verification_email(self, message: EmailMessage, subject_patterns: Optional[List[str]] = None, sender_whitelist: Optional[List[str]] = None) -> bool:
        subject_patterns = subject_patterns or DEFAULT_SUBJECT_PATTERNS
        sender_whitelist = sender_whitelist or VERIFICATION_SENDERS
        subject = (message.subject or "").lower()
        from_address = (message.from_address or "").lower()
        if sender_whitelist and any(sender.lower() in from_address for sender in sender_whitelist):
            return True
        return any(re.search(pattern, subject, re.IGNORECASE) for pattern in subject_patterns)
