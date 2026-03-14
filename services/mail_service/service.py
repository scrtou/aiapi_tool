from __future__ import annotations

from libs.contracts.mail import (
    CreateMailAccountRequest,
    DeleteMailAccountData,
    ExtractConfirmationLinkData,
    MailAccount,
    MailAccountsData,
    MailMessageData,
    MailMessagesData,
)
from libs.core.auth import assert_project_access
from libs.core.exceptions import ServiceError
from libs.core.sqlite import SQLiteMailAccountStore, SQLiteMailProviderSettingsStore
from libs.clients.duckmail_client import LinkExtractor
from services.mail_service.provider_registry import MailProviderRegistry


class MailService:
    def _provider_setting(self, provider_name: str) -> dict:
        setting = self.provider_settings_store.get(provider_name)
        if not setting:
            setting = self.provider_settings_store.save(provider_name, {"enabled": True})
        return setting

    def _ensure_provider_enabled(self, provider_name: str):
        setting = self._provider_setting(provider_name)
        if not bool(setting.get("enabled", True)):
            raise ServiceError(
                code="MAIL_PROVIDER_DISABLED",
                message=f"mail provider is disabled: {provider_name}",
                service="mail-service",
                state="provider_disabled",
                status_code=409,
            )

    def list_providers(self) -> dict:
        providers = []
        for provider_name in self.registry.list_names():
            provider = self.registry.build(provider_name)
            setting = self._provider_setting(provider_name)
            health = provider.health_check() if hasattr(provider, "health_check") else {"available": True, "error": None}
            providers.append({
                "provider_name": provider_name,
                "enabled": bool(setting.get("enabled", True)),
                "available": bool(health.get("available", False)),
                "error": health.get("error"),
                "domains": health.get("domains") or [],
                "capabilities": {
                    "can_create": True,
                    "can_list_messages": True,
                    "can_get_message": True,
                    "can_delete": True,
                },
                "updated_at": setting.get("updated_at"),
            })
        return {"providers": providers, "total": len(providers)}

    def set_provider_enabled(self, provider_name: str, enabled: bool) -> dict:
        self.registry.build(provider_name)
        setting = self.provider_settings_store.save(provider_name, {"enabled": enabled})
        return {"provider_name": provider_name, "enabled": bool(setting.get("enabled", False)), "updated_at": setting.get("updated_at")}

    def get_provider_domains(self, provider_name: str) -> dict:
        provider = self.registry.build(provider_name)
        try:
            domains = provider.list_domains() if hasattr(provider, "list_domains") else []
            return {
                "provider_name": provider_name,
                "domains": domains,
                "total": len(domains),
                "available": True,
                "error": None,
            }
        except Exception as exc:
            health = provider.health_check() if hasattr(provider, "health_check") else {"available": False, "error": str(exc)}
            domains = health.get("domains") or []
            return {
                "provider_name": provider_name,
                "domains": domains,
                "total": len(domains),
                "available": bool(health.get("available", False)),
                "error": health.get("error") or str(exc),
            }

    def check_provider_health(self, provider_name: str) -> dict:
        provider = self.registry.build(provider_name)
        health = provider.health_check() if hasattr(provider, "health_check") else {"available": True, "error": None}
        setting = self._provider_setting(provider_name)
        return {
            "provider_name": provider_name,
            "enabled": bool(setting.get("enabled", True)),
            "available": bool(health.get("available", False)),
            "error": health.get("error"),
            "domains": health.get("domains") or [],
        }

    def __init__(self):
        self.registry = MailProviderRegistry()
        self.account_store = SQLiteMailAccountStore()
        self.provider_settings_store = SQLiteMailProviderSettingsStore()
        self.provider_settings_store.ensure_defaults(self.registry.list_names())

    def create_account(self, request: CreateMailAccountRequest, *, project_id: str | None = None) -> MailAccount:
        self._ensure_provider_enabled(request.provider)
        provider = self.registry.get(request.provider)
        try:
            account = provider.create_account(
                domain=request.domain,
                pattern=request.pattern,
                expiry_time_ms=request.expiry_time_ms,
                options=request.options,
            )
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(
                code="MAIL_CREATE_FAILED",
                message=str(e),
                service="mail-service",
                state="create_account",
                retryable=True,
                details={"provider": request.provider},
                status_code=503,
            )
        if project_id:
            account = account.model_copy(update={"project_id": project_id})
        self.account_store.save(account.model_dump(mode="json"))
        return account

    def get_account(self, account_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> MailAccount:
        payload = self.account_store.get(account_id)
        account = MailAccount(**payload) if payload else None
        if not account:
            raise ServiceError(
                code="ACCOUNT_NOT_FOUND",
                message=f"mail account not found: {account_id}",
                service="mail-service",
                state="get_account",
                status_code=404,
            )
        assert_project_access(
            service="mail-service",
            resource_project_id=account.project_id,
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="mail account",
            state="get_account",
        )
        return account

    def list_messages(self, account_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> MailMessagesData:
        account = self.get_account(account_id, project_id=project_id, allow_cross_project=allow_cross_project)
        provider = self.registry.get(account.provider)
        try:
            messages = provider.list_messages(account)
            return MailMessagesData(messages=messages, total=len(messages), next_cursor=None)
        except Exception as e:
            raise ServiceError(
                code="MAIL_LIST_FAILED",
                message=str(e),
                service="mail-service",
                state="list_messages",
                retryable=True,
                details={"provider": account.provider, "account_id": account_id},
                status_code=503,
            )

    def get_message(self, account_id: str, message_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> MailMessageData:
        account = self.get_account(account_id, project_id=project_id, allow_cross_project=allow_cross_project)
        provider = self.registry.get(account.provider)
        try:
            message = provider.get_message(account, message_id)
            return MailMessageData(message=message)
        except Exception as e:
            raise ServiceError(
                code="MAIL_MESSAGE_NOT_FOUND",
                message=str(e),
                service="mail-service",
                state="get_message",
                details={"provider": account.provider, "account_id": account_id, "message_id": message_id},
                status_code=404,
            )

    def delete_account(self, account_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> DeleteMailAccountData:
        account = self.get_account(account_id, project_id=project_id, allow_cross_project=allow_cross_project)
        provider = self.registry.get(account.provider)
        try:
            provider.delete_account(account)
        finally:
            self.account_store.mark_deleted(account_id)
        return DeleteMailAccountData(deleted=True)

    def extract_confirmation_link(self, account_id: str, message_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> ExtractConfirmationLinkData:
        detail = self.get_message(account_id, message_id, project_id=project_id, allow_cross_project=allow_cross_project).message
        link = LinkExtractor.extract_confirmation_link(
            type("EmailDetail", (), {
                "id": detail.id,
                "subject": detail.subject,
                "from_address": detail.from_address,
                "text": detail.text,
                "html": [detail.html] if detail.html else [],
            })
        )
        return ExtractConfirmationLinkData(confirmation_link=link)


    def list_accounts(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        limit: int = 50,
    ) -> MailAccountsData:
        rows = self.account_store.list(
            provider=provider,
            status=status,
            project_id=project_id,
            include_all=allow_cross_project,
            limit=limit,
        )
        return MailAccountsData(accounts=[MailAccount(**row) for row in rows], total=len(rows))


    def metrics_snapshot(self) -> dict:
        providers_payload = self.list_providers()
        providers = providers_payload["providers"]
        accounts = self.account_store.list(project_id=None, include_all=True, limit=100000)
        account_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for item in accounts:
            provider_name = str(item.get("provider") or "unknown")
            account_counts[provider_name] = account_counts.get(provider_name, 0) + 1
            status = str(item.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        available_count = 0
        enabled_count = 0
        for provider in providers:
            provider["account_count"] = account_counts.get(provider["provider_name"], 0)
            if provider.get("enabled"):
                enabled_count += 1
            if provider.get("available"):
                available_count += 1
        return {
            "service": "mail-service",
            "provider_count": len(providers),
            "enabled_provider_count": enabled_count,
            "available_provider_count": available_count,
            "account_count": len(accounts),
            "account_status_counts": status_counts,
            "providers": providers,
        }
