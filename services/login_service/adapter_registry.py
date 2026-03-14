from __future__ import annotations

from libs.core.exceptions import ServiceError
from services.login_service.adapters.chayns import ChaynsLoginAdapter
from services.login_service.adapters.nexos import NexosLoginAdapter


class LoginAdapterRegistry:
    def __init__(self):
        self._adapters = {
            ChaynsLoginAdapter.site_name: ChaynsLoginAdapter,
            NexosLoginAdapter.site_name: NexosLoginAdapter,
        }

    def get(self, site: str):
        adapter_cls = self._adapters.get(site)
        if not adapter_cls:
            raise ServiceError(
                code="UNSUPPORTED_OPERATION",
                message=f"unsupported login site: {site}",
                service="login-service",
                state="adapter_registry",
                status_code=404,
            )
        return adapter_cls()
