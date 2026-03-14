from __future__ import annotations

from libs.contracts.login import LoginData, LoginRequest, LoginResult, LoginResultsData, VerifySessionData, VerifySessionRequest
from services.login_service.adapter_registry import LoginAdapterRegistry
from libs.core.sqlite import SQLiteResultStore, SQLiteSessionStore


class LoginService:
    def __init__(self):
        self.registry = LoginAdapterRegistry()
        self.session_store = SQLiteSessionStore()
        self.result_store = SQLiteResultStore()

    def login(self, request: LoginRequest, *, project_id: str | None = None) -> LoginData:
        adapter = self.registry.get(request.site)
        result = adapter.login(request.credentials, request.proxy, request.strategy)
        if project_id:
            result = result.model_copy(update={"project_id": project_id})
        identity_subject = result.identity.external_subject
        identity_user_id = result.identity.external_user_id
        payload = result.model_dump(mode="json")
        self.session_store.save(
            result.session.access_token,
            result.site,
            payload,
            identity_subject,
            identity_user_id,
            project_id=project_id,
        )
        self.result_store.save("login", result.account["email"], result.site, payload, project_id=project_id)
        return LoginData(result=result)

    def verify_session(self, request: VerifySessionRequest) -> VerifySessionData:
        adapter = self.registry.get(request.site)
        valid, identity, site_result = adapter.verify_session(request.token)
        return VerifySessionData(valid=valid, identity=identity, site_result=site_result)


    def list_results(
        self,
        *,
        site: str | None = None,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        limit: int = 50,
    ) -> LoginResultsData:
        rows = self.result_store.list(
            result_type="login",
            site=site,
            project_id=project_id,
            include_all=allow_cross_project,
            limit=limit,
        )
        return LoginResultsData(results=[LoginResult(**row) for row in rows], total=len(rows))
