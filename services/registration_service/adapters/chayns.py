from __future__ import annotations

import time
from typing import Any

from libs.contracts.mail import MailAccount
from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentity, RegistrationIdentityResult, RegistrationResult, RegistrationSession
from libs.core.exceptions import ServiceError
from services.registration_service.adapters.base import RegistrationAdapter
from services.registration_service.adapters.chayns_runtime import AutoRegister, AutoRegisterRequest, RegisterState
from services.registration_service.mail_client import MailServiceMailboxClient


class ChaynsRegistrationAdapter(RegistrationAdapter):
    site_name = "chayns"

    def _attach_mail_client(self, mail_account: MailAccount):
        return MailServiceMailboxClient(mail_account)

    def _cancel_check(self, strategy: dict | None):
        if not isinstance(strategy, dict):
            return
        callback = strategy.get("cancel_check")
        if callable(callback):
            callback()

    def register(self, identity: RegistrationIdentity, mail_account: MailAccount, proxy: ProxyLease | None = None, strategy: dict | None = None) -> RegistrationResult:
        request = AutoRegisterRequest(
            first_name=identity.first_name,
            last_name=identity.last_name,
            password=identity.password,
        )
        auto = AutoRegister(request)
        auto.start_time = time.time()
        auto.email = mail_account.address
        auto.duckmail_client = self._attach_mail_client(mail_account)
        auto.state = RegisterState.DUCKMAIL_CREATED
        auto._log(f"使用外部邮箱账户开始注册: provider={mail_account.provider}, address={mail_account.address}")

        original_check_timeout = auto._check_timeout

        def check_timeout_with_cancel():
            self._cancel_check(strategy)
            return original_check_timeout()

        auto._check_timeout = check_timeout_with_cancel

        try:
            self._cancel_check(strategy)
            auto._open_site_and_login_entry()
            auto._check_timeout()
            auto._enter_email()
            auto._check_timeout()
            is_new_user = auto._detect_branch()
            auto._check_timeout()
            if not is_new_user:
                raise ServiceError(
                    code="EMAIL_ALREADY_EXISTS",
                    message=f"email already exists: {mail_account.address}",
                    service="registration-service",
                    state="branch_detected",
                    status_code=409,
                )
            auto._fill_register_form()
            auto._check_timeout()
            confirmation_link = auto._wait_for_confirmation_link()
            auto._check_timeout()
            auto._open_confirmation_link_and_set_password(confirmation_link)
            self._cancel_check(strategy)
            result = auto._verify_login_and_extract_credentials()
            auto._call_post_register_api(result["token"])
            time.sleep(1)
            result["has_pro_access"] = auto._get_user_pro_access(result["token"], result["personid"])
            auto.state = RegisterState.COMPLETE
            return RegistrationResult(
                site=self.site_name,
                account={"email": result["email"], "password": result["password"]},
                session=RegistrationSession(
                    access_token=result["token"],
                    refresh_token=None,
                    cookies=[],
                    expires_at=None,
                ),
                identity=RegistrationIdentityResult(
                    external_subject=result["personid"],
                    external_user_id=str(result["userid"]),
                ),
                flags={"has_pro_access": result.get("has_pro_access")},
                site_result={
                    "userid": result["userid"],
                    "personid": result["personid"],
                    "has_pro_access": result.get("has_pro_access"),
                },
                artifacts=[{"type": "debug_log", "name": "autoregister", "meta": {"steps": auto.debug_logs[-20:]}}],
            )
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(
                code="REGISTRATION_SUBMIT_FAILED" if auto.state.value == "register_form" else "INTERNAL_ERROR",
                message=str(e),
                service="registration-service",
                state=auto.state.value if auto.state else None,
                retryable=auto.state in {RegisterState.WAITING_EMAIL, RegisterState.SITE_OPENED, RegisterState.LOGIN_ENTRY},
                details={"email": mail_account.address, "provider": mail_account.provider},
                status_code=422 if auto.state not in {RegisterState.FAILED} else 500,
            )
        finally:
            auto._cleanup()
