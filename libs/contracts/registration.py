from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from libs.contracts.common import ErrorBody, ProgressInfo, TaskEvent
from libs.contracts.mail import MailAccount
from libs.contracts.proxy import ProxyLease


class RegistrationIdentity(BaseModel):
    first_name: str
    last_name: str
    password: str


class RegistrationSession(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    expires_at: Optional[str] = None


class RegistrationIdentityResult(BaseModel):
    external_subject: Optional[str] = None
    external_user_id: Optional[str] = None


class RegistrationResult(BaseModel):
    project_id: Optional[str] = None
    site: str
    account: dict[str, Any]
    session: RegistrationSession
    identity: RegistrationIdentityResult
    flags: dict[str, Any] = Field(default_factory=dict)
    site_result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class RegistrationTask(BaseModel):
    task_id: str
    project_id: Optional[str] = None
    site: str
    status: str
    state: str
    progress: ProgressInfo = Field(default_factory=ProgressInfo)
    created_at: str
    updated_at: str


class CreateRegistrationTaskRequest(BaseModel):
    site: str
    identity: RegistrationIdentity
    mail_account: MailAccount
    proxy: Optional[ProxyLease] = None
    strategy: dict[str, Any] = Field(default_factory=dict)


class RegistrationTaskData(BaseModel):
    task: RegistrationTask


class RegistrationTasksData(BaseModel):
    tasks: list[RegistrationTask] = Field(default_factory=list)
    total: int = 0


class RegistrationTaskDetailData(BaseModel):
    task: RegistrationTask
    result: Optional[RegistrationResult] = None
    error: Optional[ErrorBody] = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[TaskEvent] = Field(default_factory=list)
