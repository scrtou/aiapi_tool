from __future__ import annotations

from libs.contracts.proxy import LeaseProxyData, LeaseProxyRequest, ProxyLease, ProxyLeasesData, ReleaseProxyData
from libs.core.auth import assert_project_access
from libs.core.sqlite import SQLiteProxyLeaseStore, SQLiteProxyPoolStore
from services.proxy_service.provider_registry import ProxyProviderRegistry


class ProxyService:
    def __init__(self):
        self.registry = ProxyProviderRegistry()
        self.lease_store = SQLiteProxyLeaseStore()
        self.pool_store = SQLiteProxyPoolStore()

    def lease(self, request: LeaseProxyRequest, *, project_id: str | None = None) -> LeaseProxyData:
        provider = self.registry.get_default()
        lease = provider.lease_proxy(
            scheme=request.scheme,
            country=request.country,
            sticky=request.sticky,
            ttl_seconds=request.ttl_seconds,
            tags=request.tags,
        )
        if project_id:
            lease = lease.model_copy(update={"project_id": project_id})
        self.lease_store.save(lease.model_dump(mode="json"), status="leased")
        return LeaseProxyData(lease=lease)

    def release(self, proxy_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> ReleaseProxyData:
        lease_payload = self.lease_store.get(proxy_id)
        lease = ProxyLease(**lease_payload) if lease_payload else None
        if not lease:
            return ReleaseProxyData(released=True)
        assert_project_access(
            service="proxy-service",
            resource_project_id=lease.project_id,
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="proxy lease",
            state="release_proxy",
        )
        provider = self.registry.get_default()
        provider.release_proxy(proxy_id)
        self.lease_store.mark_released(proxy_id)
        return ReleaseProxyData(released=True)


    def list_leases(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        limit: int = 50,
    ) -> ProxyLeasesData:
        rows = self.lease_store.list(
            provider=provider,
            status=status,
            project_id=project_id,
            include_all=allow_cross_project,
            limit=limit,
        )
        return ProxyLeasesData(leases=[ProxyLease(**row) for row in rows], total=len(rows))


    def list_pools(self) -> dict:
        pools = []
        entries = self.pool_store.list_entries()
        grouped: dict[str, list[dict]] = {}
        for entry in entries:
            grouped.setdefault(entry["pool_id"], []).append(entry)
        provider = self.registry.get_default()
        for pool in self.pool_store.list_pools():
            pool_entries = grouped.get(pool["pool_id"], [])
            healthy = 0
            enriched_entries = []
            for entry in pool_entries:
                health = provider.check_entry_health(entry) if hasattr(provider, "check_entry_health") else {"available": True, "status": "unknown", "error": None}
                if health.get("available"):
                    healthy += 1
                enriched_entries.append({**entry, "health": health})
            pools.append({
                **pool,
                "entry_count": len(pool_entries),
                "healthy_count": healthy,
                "entries": enriched_entries,
            })
        return {"pools": pools, "total": len(pools)}

    def create_pool(self, payload: dict) -> dict:
        return self.pool_store.save_pool(payload)

    def delete_pool(self, pool_id: str) -> dict:
        self.pool_store.delete_pool(pool_id)
        return {"deleted": True, "pool_id": pool_id}

    def set_pool_status(self, pool_id: str, enabled: bool) -> dict:
        pool = self.pool_store.get_pool(pool_id)
        if not pool:
            raise ServiceError(code="RESOURCE_NOT_FOUND", message=f"proxy pool not found: {pool_id}", service="proxy-service", state="set_pool_status", status_code=404)
        pool["status"] = "enabled" if enabled else "disabled"
        return self.pool_store.save_pool(pool)

    def create_pool_entry(self, pool_id: str, payload: dict) -> dict:
        pool = self.pool_store.get_pool(pool_id)
        if not pool:
            raise ServiceError(code="RESOURCE_NOT_FOUND", message=f"proxy pool not found: {pool_id}", service="proxy-service", state="create_pool_entry", status_code=404)
        payload = {**payload, "pool_id": pool_id}
        return self.pool_store.save_entry(payload)

    def delete_pool_entry(self, proxy_entry_id: str) -> dict:
        self.pool_store.delete_entry(proxy_entry_id)
        return {"deleted": True, "proxy_entry_id": proxy_entry_id}

    def set_pool_entry_status(self, proxy_entry_id: str, enabled: bool) -> dict:
        entry = self.pool_store.get_entry(proxy_entry_id)
        if not entry:
            raise ServiceError(code="RESOURCE_NOT_FOUND", message=f"proxy entry not found: {proxy_entry_id}", service="proxy-service", state="set_pool_entry_status", status_code=404)
        entry["status"] = "enabled" if enabled else "disabled"
        return self.pool_store.save_entry(entry)

    def check_pool_entry_health(self, proxy_entry_id: str) -> dict:
        entry = self.pool_store.get_entry(proxy_entry_id)
        if not entry:
            raise ServiceError(code="RESOURCE_NOT_FOUND", message=f"proxy entry not found: {proxy_entry_id}", service="proxy-service", state="check_pool_entry_health", status_code=404)
        provider = self.registry.get_default()
        if hasattr(provider, "check_entry_health"):
            return {"proxy_entry_id": proxy_entry_id, **provider.check_entry_health(entry)}
        return {"proxy_entry_id": proxy_entry_id, "available": True, "status": "unknown", "error": None}


    def metrics_snapshot(self) -> dict:
        pools_payload = self.list_pools()
        pools = pools_payload["pools"]
        leases = self.lease_store.list(project_id=None, include_all=True, limit=100000)
        lease_status_counts: dict[str, int] = {}
        total_entries = 0
        healthy_entries = 0
        enabled_pools = 0
        for pool in pools:
            total_entries += int(pool.get("entry_count") or 0)
            healthy_entries += int(pool.get("healthy_count") or 0)
            if pool.get("status") == "enabled":
                enabled_pools += 1
        for lease in leases:
            status = str(lease.get("status") or "unknown")
            lease_status_counts[status] = lease_status_counts.get(status, 0) + 1
        return {
            "service": "proxy-service",
            "pool_count": len(pools),
            "enabled_pool_count": enabled_pools,
            "entry_count": total_entries,
            "healthy_entry_count": healthy_entries,
            "lease_count": len(leases),
            "lease_status_counts": lease_status_counts,
            "pools": pools,
        }
