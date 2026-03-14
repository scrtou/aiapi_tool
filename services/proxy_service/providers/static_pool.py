from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from libs.contracts.proxy import ProxyLease
from libs.core.exceptions import ServiceError
from services.proxy_service.providers.base import ProxyProvider


class StaticProxyProvider(ProxyProvider):
    provider_name = "static_pool"

    def __init__(self):
        raw = os.getenv("STATIC_PROXY_POOL_JSON", "[]")
        try:
            self.pool = json.loads(raw)
        except Exception:
            self.pool = []
        self._leases: dict[str, ProxyLease] = {}

    def lease_proxy(self, *, scheme=None, country=None, sticky=False, ttl_seconds=600, tags=None) -> ProxyLease:
        if not self.pool:
            raise ServiceError(
                code="PROXY_NOT_AVAILABLE",
                message="static proxy pool is empty",
                service="proxy-service",
                state="lease_proxy",
                retryable=True,
                status_code=503,
            )
        item = self.pool[0]
        lease = ProxyLease(
            proxy_id=f"px_{secrets.token_hex(6)}",
            provider=self.provider_name,
            scheme=item["scheme"],
            host=item["host"],
            port=int(item["port"]),
            username=item.get("username"),
            password=item.get("password"),
            country=item.get("country"),
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            meta={"sticky": sticky, "tags": tags or []},
        )
        self._leases[lease.proxy_id] = lease
        return lease

    def release_proxy(self, proxy_id: str) -> bool:
        self._leases.pop(proxy_id, None)
        return True
