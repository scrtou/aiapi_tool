from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ProxyLease(BaseModel):
    project_id: Optional[str] = None
    proxy_id: str
    provider: str
    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    expires_at: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class LeaseProxyRequest(BaseModel):
    scheme: list[str] = Field(default_factory=list)
    country: list[str] = Field(default_factory=list)
    sticky: bool = False
    ttl_seconds: int = 600
    tags: list[str] = Field(default_factory=list)


class LeaseProxyData(BaseModel):
    lease: ProxyLease


class ProxyLeasesData(BaseModel):
    leases: list[ProxyLease] = Field(default_factory=list)
    total: int = 0


class ReleaseProxyData(BaseModel):
    released: bool = True


class ReportProxyRequest(BaseModel):
    status: str
    reason: Optional[str] = None
