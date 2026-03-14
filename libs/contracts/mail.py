from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MailAccount(BaseModel):
    project_id: Optional[str] = None
    provider: str
    account_id: str
    address: str
    password: Optional[str] = None
    status: str = "active"
    expires_at: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class MailMessageSummary(BaseModel):
    id: str
    from_address: str = ""
    from_name: str = ""
    subject: str = ""
    received_at: str = ""
    seen: bool = False


class MailMessageDetail(BaseModel):
    id: str
    from_address: str = ""
    subject: str = ""
    text: str = ""
    html: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class CreateMailAccountRequest(BaseModel):
    provider: str
    domain: Optional[str] = None
    pattern: Optional[str] = None
    expiry_time_ms: Optional[int] = None
    options: dict[str, Any] = Field(default_factory=dict)


class CreateMailAccountData(BaseModel):
    account: MailAccount


class MailAccountsData(BaseModel):
    accounts: list[MailAccount] = Field(default_factory=list)
    total: int = 0


class MailMessagesData(BaseModel):
    messages: list[MailMessageSummary] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    total: int = 0


class MailMessageData(BaseModel):
    message: MailMessageDetail


class DeleteMailAccountData(BaseModel):
    deleted: bool = True


class ExtractConfirmationLinkRequest(BaseModel):
    message_id: str
    ruleset: str = "generic"


class ExtractConfirmationLinkData(BaseModel):
    confirmation_link: Optional[str] = None
