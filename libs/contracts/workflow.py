from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.common import ErrorBody, ProgressInfo, TaskEvent
from libs.contracts.login import LoginCredentials
from libs.contracts.registration import RegistrationIdentity


class WorkflowTask(BaseModel):
    task_id: str
    project_id: Optional[str] = None
    workflow_type: str
    site: str
    status: str
    state: str
    progress: ProgressInfo = Field(default_factory=ProgressInfo)
    created_at: str
    updated_at: str


class MailPolicy(BaseModel):
    providers: list[str] = Field(default_factory=list)
    domain_preference: list[str] = Field(default_factory=list)
    expiry_time_ms: Optional[int] = None


class ProxyPolicy(BaseModel):
    enabled: bool = False
    lease_request: dict[str, Any] = Field(default_factory=dict)


class WorkflowStrategy(BaseModel):
    model_config = ConfigDict(extra="allow")

    registration_mode: str = "api_first"
    login_mode: str = "api_first"
    timeout_seconds: int = 360


class CallbackConfig(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 15
    secret: Optional[str] = None
    max_attempts: int = 3
    retry_backoff_seconds: int = 3


class RegisterWorkflowRequest(BaseModel):
    site: str
    mail_policy: MailPolicy
    proxy_policy: ProxyPolicy = Field(default_factory=ProxyPolicy)
    identity: RegistrationIdentity
    strategy: WorkflowStrategy = Field(default_factory=WorkflowStrategy)
    callback: Optional[CallbackConfig] = None


class LoginWorkflowRequest(BaseModel):
    site: str
    credentials: LoginCredentials
    proxy_policy: ProxyPolicy = Field(default_factory=ProxyPolicy)
    strategy: WorkflowStrategy = Field(default_factory=WorkflowStrategy)
    callback: Optional[CallbackConfig] = None


class WorkflowTaskData(BaseModel):
    task_id: str
    status: str
    state: str


class WorkflowTasksData(BaseModel):
    tasks: list[WorkflowTask] = Field(default_factory=list)
    total: int = 0


class WorkflowTaskDetailData(BaseModel):
    task: WorkflowTask
    result: Optional[dict[str, Any]] = None
    error: Optional[ErrorBody] = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[TaskEvent] = Field(default_factory=list)
