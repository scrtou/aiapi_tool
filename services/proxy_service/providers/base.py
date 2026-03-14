from __future__ import annotations

from abc import ABC, abstractmethod

from libs.contracts.proxy import ProxyLease


class ProxyProvider(ABC):
    provider_name: str

    @abstractmethod
    def lease_proxy(self, *, scheme: list[str] | None = None, country: list[str] | None = None, sticky: bool = False, ttl_seconds: int = 600, tags: list[str] | None = None) -> ProxyLease:
        raise NotImplementedError

    @abstractmethod
    def release_proxy(self, proxy_id: str) -> bool:
        raise NotImplementedError
