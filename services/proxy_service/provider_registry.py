from __future__ import annotations

from services.proxy_service.providers.managed_pool import ManagedProxyProvider


class ProxyProviderRegistry:
    def __init__(self):
        self._provider = ManagedProxyProvider()

    def get_default(self):
        return self._provider
