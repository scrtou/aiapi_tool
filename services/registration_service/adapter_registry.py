from __future__ import annotations

from libs.core.exceptions import ServiceError
from services.registration_service.adapters.chayns import ChaynsRegistrationAdapter
from services.registration_service.adapters.nexos import NexosRegistrationAdapter


class RegistrationAdapterRegistry:
    def __init__(self):
        self._adapters = {
            ChaynsRegistrationAdapter.site_name: ChaynsRegistrationAdapter,
            NexosRegistrationAdapter.site_name: NexosRegistrationAdapter,
        }

    def get(self, site: str):
        adapter_cls = self._adapters.get(site)
        if not adapter_cls:
            raise ServiceError(
                code="UNSUPPORTED_OPERATION",
                message=f"unsupported registration site: {site}",
                service="registration-service",
                state="adapter_registry",
                status_code=404,
            )
        return adapter_cls()
