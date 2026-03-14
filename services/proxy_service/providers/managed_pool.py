from __future__ import annotations

import json
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone

from libs.contracts.proxy import ProxyLease
from libs.core.exceptions import ServiceError
from libs.core.sqlite import SQLiteProxyPoolStore
from services.proxy_service.providers.base import ProxyProvider


class ManagedProxyProvider(ProxyProvider):
    provider_name = "managed_pool"

    def __init__(self):
        self.pool_store = SQLiteProxyPoolStore()
        self._bootstrap_from_env()

    def _bootstrap_from_env(self):
        existing_pools = self.pool_store.list_pools()
        if existing_pools:
            return
        raw = os.getenv("STATIC_PROXY_POOL_JSON", "[]")
        try:
            items = json.loads(raw)
        except Exception:
            items = []
        if not items:
            return
        pool_id = "bootstrap_static_pool"
        self.pool_store.save_pool({
            "pool_id": pool_id,
            "name": "Bootstrap Static Pool",
            "source": "env",
            "description": "Imported from STATIC_PROXY_POOL_JSON",
            "status": "enabled",
        })
        for index, item in enumerate(items, start=1):
            self.pool_store.save_entry({
                "proxy_entry_id": f"bootstrap_proxy_{index}",
                "pool_id": pool_id,
                "provider": item.get("provider") or self.provider_name,
                "name": item.get("name") or f"Proxy {index}",
                "scheme": item.get("scheme", "http"),
                "host": item.get("host"),
                "port": int(item.get("port")),
                "username": item.get("username"),
                "password": item.get("password"),
                "country": item.get("country"),
                "status": "enabled",
                "meta": item.get("meta", {}),
            })

    def _candidate_entries(self, *, scheme=None, country=None):
        pools = {pool["pool_id"]: pool for pool in self.pool_store.list_pools() if pool.get("status") == "enabled"}
        entries = []
        for entry in self.pool_store.list_entries():
            if entry.get("status") != "enabled":
                continue
            if entry.get("pool_id") not in pools:
                continue
            if scheme and entry.get("scheme") not in scheme:
                continue
            if country and entry.get("country") and entry.get("country") not in country:
                continue
            entries.append(entry)
        return entries

    def lease_proxy(self, *, scheme=None, country=None, sticky=False, ttl_seconds=600, tags=None) -> ProxyLease:
        entries = self._candidate_entries(scheme=scheme, country=country)
        if not entries:
            raise ServiceError(
                code="PROXY_NOT_AVAILABLE",
                message="no enabled proxy entry is available",
                service="proxy-service",
                state="lease_proxy",
                retryable=True,
                status_code=503,
            )
        entry = entries[0]
        pool = self.pool_store.get_pool(entry["pool_id"]) or {}
        return ProxyLease(
            proxy_id=f"px_{secrets.token_hex(6)}",
            provider=entry.get("provider") or self.provider_name,
            scheme=entry["scheme"],
            host=entry["host"],
            port=int(entry["port"]),
            username=entry.get("username"),
            password=entry.get("password"),
            country=entry.get("country"),
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            meta={
                "sticky": sticky,
                "tags": tags or [],
                "pool_id": entry["pool_id"],
                "proxy_entry_id": entry["proxy_entry_id"],
                "pool_name": pool.get("name"),
                "source": pool.get("source"),
            },
        )

    def release_proxy(self, proxy_id: str) -> bool:
        return True

    def check_entry_health(self, entry: dict) -> dict:
        host = entry.get("host")
        port = int(entry.get("port") or 0)
        if not host or not port:
            return {"available": False, "status": "invalid", "error": "missing host or port"}
        try:
            with socket.create_connection((host, port), timeout=5):
                return {"available": True, "status": "reachable", "error": None}
        except Exception as exc:
            return {"available": False, "status": "unreachable", "error": str(exc)}
