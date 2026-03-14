from __future__ import annotations

from libs.core.exceptions import ServiceError
from services.mail_service.providers.duckmail import DuckMailProvider
from services.mail_service.providers.gptmail import GPTMailProvider
from services.mail_service.providers.mailcx import MailCxProvider
from services.mail_service.providers.moemail import MoeMailProvider
from services.mail_service.providers.smailpro_api import SmailProApiProvider
from services.mail_service.providers.smailpro_web import SmailProWebProvider


class MailProviderRegistry:
    def __init__(self):
        self._providers = {
            MoeMailProvider.provider_name: MoeMailProvider,
            GPTMailProvider.provider_name: GPTMailProvider,
            DuckMailProvider.provider_name: DuckMailProvider,
            MailCxProvider.provider_name: MailCxProvider,
            SmailProApiProvider.provider_name: SmailProApiProvider,
            SmailProWebProvider.provider_name: SmailProWebProvider,
        }

    def get(self, provider_name: str):
        provider_cls = self._providers.get(provider_name)
        if not provider_cls:
            raise ServiceError(
                code="MAIL_PROVIDER_UNAVAILABLE",
                message=f"unknown mail provider: {provider_name}",
                service="mail-service",
                state="provider_registry",
                status_code=404,
            )
        return provider_cls()


    def list_names(self) -> list[str]:
        return list(self._providers.keys())

    def build(self, provider_name: str):
        return self.get(provider_name)
