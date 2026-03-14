from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from libs.clients.nexos_client import NEXOS_BASE_URL, NexosAuthClient, flow_message_texts, flow_messages, is_flow_payload
from libs.contracts.mail import MailAccount
from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentity, RegistrationIdentityResult, RegistrationResult, RegistrationSession
from libs.core.config import env_bool, env_int, env_str
from libs.core.exceptions import ServiceError
from services.registration_service.adapters.base import RegistrationAdapter
from services.registration_service.mail_client import MailServiceMailboxClient
from services.shared.nexos_drission_flow import NexosDrissionFlow


NEXOS_TURNSTILE_SITE_KEY = env_str("NEXOS_TURNSTILE_SITE_KEY", "0x4AAAAAACZj49I1vZV-qxTZ") or "0x4AAAAAACZj49I1vZV-qxTZ"
NEXOS_TURNSTILE_PAGE_URL = env_str(
    "NEXOS_TURNSTILE_PAGE_URL",
    f"{NEXOS_BASE_URL.rstrip('/')}/authorization/registration",
) or f"{NEXOS_BASE_URL.rstrip('/')}/authorization/registration"
NEXOS_TURNSTILE_TIMEOUT_SECONDS = env_int("NEXOS_TURNSTILE_TIMEOUT_SECONDS", 180)
NEXOS_TURNSTILE_POLL_INTERVAL_SECONDS = env_int("NEXOS_TURNSTILE_POLL_INTERVAL_SECONDS", 5)
NEXOS_MAIL_WAIT_SECONDS = env_int("NEXOS_MAIL_WAIT_SECONDS", 240)
NEXOS_MAIL_POLL_INTERVAL_SECONDS = env_int("NEXOS_MAIL_POLL_INTERVAL_SECONDS", 5)
NEXOS_CAPTCHA_PROVIDER = env_str("NEXOS_CAPTCHA_PROVIDER")
NEXOS_2CAPTCHA_API_KEY = env_str("NEXOS_2CAPTCHA_API_KEY")
NEXOS_CAPSOLVER_API_KEY = env_str("NEXOS_CAPSOLVER_API_KEY")
NEXOS_ENABLE_BROWSER_TURNSTILE_FALLBACK = env_bool("NEXOS_ENABLE_BROWSER_TURNSTILE_FALLBACK", True)
NEXOS_BROWSER_TURNSTILE_HEADLESS = env_bool("NEXOS_BROWSER_TURNSTILE_HEADLESS", True)
NEXOS_BROWSER_TURNSTILE_WAIT_SECONDS = env_int("NEXOS_BROWSER_TURNSTILE_WAIT_SECONDS", 45)


class NexosRegistrationAdapter(RegistrationAdapter):
    site_name = "nexos"

    def _browser_mode_requested(self, strategy: dict | None) -> bool:
        if not isinstance(strategy, dict):
            return False
        mode = str(strategy.get("mode") or strategy.get("registration_mode") or "").strip().lower()
        if mode in {"browser", "drission", "drissionpage", "ui", "browser_ui", "headful"}:
            return True
        config = self._captcha_config(strategy)
        provider = str(config.get("provider") or "").strip().lower()
        return provider in {"browser", "camoufox", "playwright", "browser_ui", "ui", "drission", "drissionpage"}

    def _log(self, logs: list[str], message: str):
        logs.append(f"{datetime.now(timezone.utc).isoformat()} - {message}")

    def _cancel_check(self, strategy: dict | None):
        if not isinstance(strategy, dict):
            return
        callback = strategy.get("cancel_check")
        if callable(callback):
            callback()

    def _captcha_config(self, strategy: dict | None) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if isinstance(strategy, dict):
            for key in ("captcha", "nexos"):
                value = strategy.get(key)
                if isinstance(value, dict):
                    config.update(value)
            for key in ("turnstile_token", "provider", "api_key", "timeout_seconds", "poll_interval_seconds", "page_url", "site_key", "browser_headless"):
                value = strategy.get(key)
                if value not in (None, ""):
                    config.setdefault(key, value)
        return config

    def _captcha_provider(self, strategy: dict | None) -> str:
        config = self._captcha_config(strategy)
        provider = str(config.get("provider") or NEXOS_CAPTCHA_PROVIDER or "").strip().lower()
        if not provider:
            if NEXOS_CAPSOLVER_API_KEY:
                provider = "capsolver"
            elif NEXOS_2CAPTCHA_API_KEY:
                provider = "2captcha"
            elif NEXOS_ENABLE_BROWSER_TURNSTILE_FALLBACK:
                provider = "browser"
        return provider

    def _message_blob(self, payload: dict[str, Any] | None) -> str:
        return " ".join(text.strip() for text in flow_message_texts(payload)).lower()

    def _has_password_step(self, payload: dict[str, Any] | None) -> bool:
        if not is_flow_payload(payload):
            return False
        nodes = payload.get("ui", {}).get("nodes") or []
        return any(isinstance(node, dict) and (node.get("attributes") or {}).get("name") == "password" for node in nodes)

    def _is_email_exists_error(self, payload: dict[str, Any] | None) -> bool:
        text = self._message_blob(payload)
        phrases = [
            "already exists",
            "already in use",
            "already taken",
            "use a different",
            "registered with this email",
        ]
        return any(phrase in text for phrase in phrases)

    def _is_security_verification_error(self, payload: dict[str, Any] | None) -> bool:
        return "security verification failed" in self._message_blob(payload)

    def _is_invalid_verification_code(self, payload: dict[str, Any] | None) -> bool:
        return "verification code is invalid" in self._message_blob(payload)

    def _is_registration_success(self, status_code: int, payload: dict[str, Any] | None) -> bool:
        if status_code not in {200, 201}:
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("state") == "passed_challenge":
            return True
        if payload.get("session") or payload.get("session_token"):
            return True
        if payload.get("continue_with"):
            return True
        if is_flow_payload(payload):
            return "check your inbox" in self._message_blob(payload)
        return True

    def _is_verification_sent(self, payload: dict[str, Any] | None) -> bool:
        return isinstance(payload, dict) and payload.get("state") == "sent_email"

    def _is_verification_success(self, payload: dict[str, Any] | None) -> bool:
        return isinstance(payload, dict) and payload.get("state") == "passed_challenge"

    def _extract_session_handle(self, client: NexosAuthClient, payload: dict[str, Any] | None) -> str | None:
        token = None
        if isinstance(payload, dict):
            token = payload.get("session_token")
            if not token:
                session = payload.get("session")
                if isinstance(session, dict):
                    token = session.get("session_token") or session.get("token")
            if not token:
                items = payload.get("continue_with") or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    token = item.get("session_token") or item.get("ory_session_token") or item.get("token")
                    if token:
                        break
        cookie_header = client.current_cookie_header()
        handle = NexosAuthClient.encode_session_handle(token, cookie_header=cookie_header or None)
        return handle or None

    def _extract_email(self, whoami_payload: dict[str, Any]) -> str | None:
        identity = whoami_payload.get("identity") or {}
        traits = identity.get("traits") or {}
        email = traits.get("email")
        if isinstance(email, str) and email:
            return email
        addresses = identity.get("verifiable_addresses") or []
        for address in addresses:
            if isinstance(address, dict) and address.get("value"):
                return str(address.get("value"))
        return None

    def _extract_email_verified(self, whoami_payload: dict[str, Any], email: str | None) -> bool | None:
        identity = whoami_payload.get("identity") or {}
        addresses = identity.get("verifiable_addresses") or []
        for address in addresses:
            if not isinstance(address, dict):
                continue
            value = address.get("value")
            if email and value and str(value).lower() != email.lower():
                continue
            if address.get("verified") is True:
                return True
            status = str(address.get("status") or "").lower()
            if status in {"completed", "sent", "verified"}:
                return status == "completed" or status == "verified"
        return None

    def _identity_from_whoami(self, whoami_payload: dict[str, Any]) -> tuple[RegistrationIdentityResult, dict[str, Any], dict[str, Any]]:
        identity = whoami_payload.get("identity") or {}
        identity_id = str(identity.get("id") or "") or None
        session_id = str(whoami_payload.get("id") or "") or None
        email = self._extract_email(whoami_payload)
        email_verified = self._extract_email_verified(whoami_payload, email)
        result_identity = RegistrationIdentityResult(
            external_subject=identity_id,
            external_user_id=identity_id,
        )
        flags = {"email_verified": email_verified}
        site_result = {
            "identity_id": identity_id,
            "session_id": session_id,
            "email": email,
            "email_verified": email_verified,
        }
        return result_identity, flags, site_result

    def _solve_with_2captcha(self, api_key: str, site_key: str, page_url: str, timeout_seconds: int, poll_interval_seconds: int, strategy: dict | None, logs: list[str]) -> str:
        create_response = requests.post(
            "https://api.2captcha.com/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                },
            },
            timeout=30,
        )
        create_response.raise_for_status()
        payload = create_response.json()
        if payload.get("errorId"):
            raise ServiceError(
                code="CAPTCHA_SOLVER_FAILED",
                message=str(payload.get("errorDescription") or payload.get("errorCode") or "2captcha createTask failed"),
                service="registration-service",
                state="captcha_create_task",
                retryable=False,
                status_code=422,
            )
        task_id = payload.get("taskId")
        if not task_id:
            raise ServiceError(
                code="CAPTCHA_SOLVER_FAILED",
                message="2captcha taskId missing",
                service="registration-service",
                state="captcha_create_task",
                retryable=False,
                status_code=422,
            )
        self._log(logs, f"2captcha 任务已创建: task_id={task_id}")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            self._cancel_check(strategy)
            time.sleep(poll_interval_seconds)
            poll_response = requests.post(
                "https://api.2captcha.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=30,
            )
            poll_response.raise_for_status()
            poll_payload = poll_response.json()
            if poll_payload.get("errorId"):
                raise ServiceError(
                    code="CAPTCHA_SOLVER_FAILED",
                    message=str(poll_payload.get("errorDescription") or poll_payload.get("errorCode") or "2captcha getTaskResult failed"),
                    service="registration-service",
                    state="captcha_poll_task",
                    retryable=False,
                    status_code=422,
                )
            if poll_payload.get("status") != "ready":
                continue
            solution = poll_payload.get("solution") or {}
            token = solution.get("token")
            if token:
                return str(token)
        raise ServiceError(
            code="CAPTCHA_SOLVER_TIMEOUT",
            message="2captcha turnstile solving timed out",
            service="registration-service",
            state="captcha_poll_task",
            retryable=True,
            status_code=504,
        )

    def _solve_with_capsolver(self, api_key: str, site_key: str, page_url: str, timeout_seconds: int, poll_interval_seconds: int, strategy: dict | None, logs: list[str]) -> str:
        create_response = requests.post(
            "https://api.capsolver.com/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                },
            },
            timeout=30,
        )
        create_response.raise_for_status()
        payload = create_response.json()
        if payload.get("errorId") or payload.get("errorCode"):
            raise ServiceError(
                code="CAPTCHA_SOLVER_FAILED",
                message=str(payload.get("errorDescription") or payload.get("errorMessage") or payload.get("errorCode") or "capsolver createTask failed"),
                service="registration-service",
                state="captcha_create_task",
                retryable=False,
                status_code=422,
            )
        task_id = payload.get("taskId")
        if not task_id:
            raise ServiceError(
                code="CAPTCHA_SOLVER_FAILED",
                message="capsolver taskId missing",
                service="registration-service",
                state="captcha_create_task",
                retryable=False,
                status_code=422,
            )
        self._log(logs, f"capsolver 任务已创建: task_id={task_id}")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            self._cancel_check(strategy)
            time.sleep(poll_interval_seconds)
            poll_response = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=30,
            )
            poll_response.raise_for_status()
            poll_payload = poll_response.json()
            if poll_payload.get("errorId") or poll_payload.get("errorCode"):
                raise ServiceError(
                    code="CAPTCHA_SOLVER_FAILED",
                    message=str(poll_payload.get("errorDescription") or poll_payload.get("errorMessage") or poll_payload.get("errorCode") or "capsolver getTaskResult failed"),
                    service="registration-service",
                    state="captcha_poll_task",
                    retryable=False,
                    status_code=422,
                )
            if poll_payload.get("status") != "ready":
                continue
            solution = poll_payload.get("solution") or {}
            token = solution.get("token")
            if token:
                return str(token)
        raise ServiceError(
            code="CAPTCHA_SOLVER_TIMEOUT",
            message="capsolver turnstile solving timed out",
            service="registration-service",
            state="captcha_poll_task",
            retryable=True,
            status_code=504,
        )

    def _solve_with_browser(self, strategy: dict | None, logs: list[str]) -> str | None:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.action_chains import ActionChains
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
            from webdriver_manager.chrome import ChromeDriverManager
        except Exception as exc:
            self._log(logs, f"浏览器验证码回退不可用: {exc}")
            return None

        options = Options()
        binary_path = env_str("CHROME_BINARY")
        if binary_path:
            options.binary_location = binary_path
        elif os.path.exists("/usr/bin/chromium"):
            options.binary_location = "/usr/bin/chromium"
        elif os.path.exists("/usr/bin/google-chrome"):
            options.binary_location = "/usr/bin/google-chrome"

        config = self._captcha_config(strategy)
        headless = bool(config.get("browser_headless", NEXOS_BROWSER_TURNSTILE_HEADLESS))
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1400,1200")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        if os.path.exists("/usr/bin/chromedriver"):
            service = Service("/usr/bin/chromedriver")
        elif os.path.exists("/usr/local/bin/chromedriver"):
            service = Service("/usr/local/bin/chromedriver")
        else:
            service = Service(ChromeDriverManager().install())

        driver = None
        try:
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            driver.get(config.get("page_url") or NEXOS_TURNSTILE_PAGE_URL)
            wait = WebDriverWait(driver, 20)
            try:
                wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'OK, understood')]"))).click()
            except Exception:
                pass

            deadline = time.time() + NEXOS_BROWSER_TURNSTILE_WAIT_SECONDS
            clicked = False
            while time.time() < deadline:
                self._cancel_check(strategy)
                token_state = driver.execute_script(
                    """
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    const id = input && input.id ? input.id.replace(/^cf-chl-widget-/, '').replace(/_response$/, '') : null;
                    let response = null;
                    if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                        try {
                            response = id ? window.turnstile.getResponse(id) : window.turnstile.getResponse();
                        } catch (error) {
                            response = null;
                        }
                        try {
                            if (!response && typeof window.turnstile.execute === 'function') {
                                if (id) {
                                    window.turnstile.execute(id);
                                } else {
                                    window.turnstile.execute();
                                }
                            }
                        } catch (error) {
                        }
                    }
                    return {
                        value: input ? input.value : null,
                        response,
                    };
                    """
                ) or {}
                token = token_state.get("value") or token_state.get("response")
                if token:
                    return str(token)

                if not clicked:
                    try:
                        modal = driver.find_element(By.XPATH, "//*[contains(., 'verify you are human before continuing')]/ancestor::div[contains(@class,'shadow-xlg') or contains(@class,'rounded-md')][1]")
                        rect = modal.rect
                        target_x = rect["x"] + 45
                        target_y = rect["y"] + 76
                        ActionChains(driver).move_by_offset(target_x, target_y).click().perform()
                        ActionChains(driver).move_by_offset(-target_x, -target_y).perform()
                        clicked = True
                    except Exception:
                        pass

                time.sleep(1)
        except Exception as exc:
            self._log(logs, f"浏览器验证码回退失败: {exc}")
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
        return None

    def _resolve_turnstile_token(self, strategy: dict | None, logs: list[str]) -> str:
        config = self._captcha_config(strategy)
        token = config.get("turnstile_token") or config.get("token")
        if token:
            self._log(logs, "使用外部传入的 turnstile token")
            return str(token)

        provider = self._captcha_provider(strategy)
        timeout_seconds = int(config.get("timeout_seconds") or NEXOS_TURNSTILE_TIMEOUT_SECONDS)
        poll_interval_seconds = int(config.get("poll_interval_seconds") or NEXOS_TURNSTILE_POLL_INTERVAL_SECONDS)
        site_key = str(config.get("site_key") or NEXOS_TURNSTILE_SITE_KEY)
        page_url = str(config.get("page_url") or NEXOS_TURNSTILE_PAGE_URL)

        if provider in {"2captcha", "two_captcha"}:
            api_key = str(config.get("api_key") or NEXOS_2CAPTCHA_API_KEY or "").strip()
            if not api_key:
                raise ServiceError(
                    code="CAPTCHA_SOLVER_NOT_CONFIGURED",
                    message="2captcha api key is missing",
                    service="registration-service",
                    state="captcha_config",
                    retryable=False,
                    status_code=422,
                )
            self._log(logs, "使用 2captcha 获取 turnstile token")
            return self._solve_with_2captcha(api_key, site_key, page_url, timeout_seconds, poll_interval_seconds, strategy, logs)

        if provider == "capsolver":
            api_key = str(config.get("api_key") or NEXOS_CAPSOLVER_API_KEY or "").strip()
            if not api_key:
                raise ServiceError(
                    code="CAPTCHA_SOLVER_NOT_CONFIGURED",
                    message="capsolver api key is missing",
                    service="registration-service",
                    state="captcha_config",
                    retryable=False,
                    status_code=422,
                )
            self._log(logs, "使用 capsolver 获取 turnstile token")
            return self._solve_with_capsolver(api_key, site_key, page_url, timeout_seconds, poll_interval_seconds, strategy, logs)

        if provider in {"browser", "selenium"}:
            self._log(logs, "尝试使用浏览器回退获取 turnstile token")
            browser_token = self._solve_with_browser(strategy, logs)
            if browser_token:
                return browser_token
            raise ServiceError(
                code="CAPTCHA_SOLVER_FAILED",
                message="browser fallback could not obtain a turnstile token",
                service="registration-service",
                state="captcha_browser",
                retryable=False,
                status_code=422,
            )

        raise ServiceError(
            code="CAPTCHA_SOLVER_NOT_CONFIGURED",
            message="turnstile token is required; provide strategy.captcha.turnstile_token or configure 2captcha/capsolver/browser fallback",
            service="registration-service",
            state="captcha_config",
            retryable=False,
            status_code=422,
        )

    def _extract_verification_code(self, detail) -> str | None:
        parts = [detail.subject or "", detail.text or "", *(detail.html or [])]
        body = "\n".join(parts)
        normalized = re.sub(r"<[^>]+>", " ", body)
        normalized = re.sub(r"\s+", " ", normalized)
        patterns = [
            r"(?:verification|verify|confirmation|confirm)[^\d]{0,40}(\d{6,8})",
            r"(?:code|token|otp|passcode)[^\d]{0,20}(\d{6,8})",
            r"(?<!\d)(\d{6})(?!\d)",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                return str(match.group(1))
        return None

    def _wait_for_verification_code(self, mail_client: MailServiceMailboxClient, known_message_ids: set[str], strategy: dict | None, logs: list[str]) -> str:
        deadline = time.time() + NEXOS_MAIL_WAIT_SECONDS
        inspected_ids = set(known_message_ids)
        while time.time() < deadline:
            self._cancel_check(strategy)
            messages = mail_client.list_messages()
            for message in messages:
                if message.id in inspected_ids:
                    continue
                inspected_ids.add(message.id)
                detail = mail_client.get_message(message.id)
                code = self._extract_verification_code(detail)
                if code:
                    self._log(logs, f"收到验证邮件: message_id={message.id}, subject={message.subject}")
                    return code
            time.sleep(NEXOS_MAIL_POLL_INTERVAL_SECONDS)
        raise ServiceError(
            code="VERIFICATION_EMAIL_TIMEOUT",
            message="timed out waiting for nexos verification email",
            service="registration-service",
            state="verification_email",
            retryable=True,
            status_code=504,
        )

    def _login_and_load_identity(self, client: NexosAuthClient, email: str, password: str) -> tuple[str, RegistrationIdentityResult, dict[str, Any], dict[str, Any]]:
        flow = client.create_login_flow()
        status_code, payload = client.submit_login_password(flow, email=email, password=password)
        if status_code == 400 and "invalid" in self._message_blob(payload):
            raise ServiceError(
                code="LOGIN_INVALID_CREDENTIALS",
                message="invalid credentials",
                service="registration-service",
                state="login",
                retryable=False,
                status_code=401,
            )
        if status_code not in {200, 201}:
            raise ServiceError(
                code="LOGIN_API_FAILED",
                message=" ".join(flow_message_texts(payload)) or str(payload),
                service="registration-service",
                state="login",
                retryable=status_code >= 500,
                details={"status_code": status_code},
                status_code=503 if status_code >= 500 else 422,
            )
        session_handle = self._extract_session_handle(client, payload)
        if not session_handle:
            raise ServiceError(
                code="LOGIN_TOKEN_NOT_RETURNED",
                message="nexos login did not return a reusable session",
                service="registration-service",
                state="login",
                retryable=False,
                status_code=502,
            )
        valid, whoami_payload = client.whoami(session_handle)
        if not valid or not whoami_payload:
            raise ServiceError(
                code="LOGIN_VERIFY_FAILED",
                message="nexos login session could not be verified",
                service="registration-service",
                state="login_whoami",
                retryable=False,
                status_code=502,
            )
        identity, flags, site_result = self._identity_from_whoami(whoami_payload)
        return session_handle, identity, flags, site_result

    def register(self, identity: RegistrationIdentity, mail_account: MailAccount, proxy: ProxyLease | None = None, strategy: dict | None = None) -> RegistrationResult:
        logs: list[str] = []
        client = NexosAuthClient(proxy=proxy)
        mailbox = MailServiceMailboxClient(mail_account)
        used_browser_flow = False
        browser_meta: dict[str, Any] = {}

        self._cancel_check(strategy)
        self._log(logs, f"开始 nexos 注册: email={mail_account.address}")

        turnstile_config = self._captcha_config(strategy)
        turnstile_provider = self._captcha_provider(strategy)
        if self._browser_mode_requested(strategy) and not (turnstile_config.get("turnstile_token") or turnstile_config.get("token")):
            self._log(logs, "使用 Drission 浏览器完整流程执行 nexos 注册/验证/登录")
            browser_result = NexosDrissionFlow(proxy=proxy).register_and_login(identity, mail_account, mailbox, strategy, logs)
            used_browser_flow = True
            browser_meta = browser_result.get("browser_meta") or {}
            whoami_payload = browser_result.get("whoami_payload") or {}
            result_identity, flags, site_result = self._identity_from_whoami(whoami_payload)
            flags = {**flags, "browser_registration": True, "browser_login": True}
            site_result = {
                **site_result,
                "browser_registration": True,
                "browser_login": True,
                "browser_meta": browser_meta,
                "chat_id": browser_result.get("chat_id"),
                "current_url": browser_result.get("current_url"),
            }
            return RegistrationResult(
                site=self.site_name,
                account={"email": mail_account.address, "password": identity.password},
                session=RegistrationSession(
                    access_token=str(browser_result.get("session_handle") or ""),
                    refresh_token=None,
                    cookies=browser_result.get("cookies") or [],
                    expires_at=None,
                ),
                identity=result_identity,
                flags=flags,
                site_result=site_result,
                artifacts=[{"type": "debug_log", "name": "nexos_register_browser", "meta": {"steps": logs[-80:]}}],
            )
        else:
            registration_flow = client.create_registration_flow()
            status_code, password_flow = client.submit_registration_profile(
                registration_flow,
                email=mail_account.address,
                first_name=identity.first_name,
                last_name=identity.last_name,
            )
            if not self._has_password_step(password_flow):
                if self._is_email_exists_error(password_flow):
                    raise ServiceError(
                        code="EMAIL_ALREADY_EXISTS",
                        message=f"email already exists: {mail_account.address}",
                        service="registration-service",
                        state="registration_profile",
                        retryable=False,
                        status_code=409,
                    )
                raise ServiceError(
                    code="REGISTRATION_PROFILE_FAILED",
                    message=" ".join(flow_message_texts(password_flow)) or f"unexpected registration status: {status_code}",
                    service="registration-service",
                    state="registration_profile",
                    retryable=status_code >= 500,
                    details={"status_code": status_code},
                    status_code=503 if status_code >= 500 else 422,
                )
            self._log(logs, "注册 profile 步骤已完成，准备提交密码")

            turnstile_token = self._resolve_turnstile_token(strategy, logs)
            self._cancel_check(strategy)

            status_code, registration_result = client.submit_registration_password(
                password_flow,
                email=mail_account.address,
                first_name=identity.first_name,
                last_name=identity.last_name,
                password=identity.password,
                turnstile_token=turnstile_token,
            )

            if self._is_email_exists_error(registration_result):
                raise ServiceError(
                    code="EMAIL_ALREADY_EXISTS",
                    message=f"email already exists: {mail_account.address}",
                    service="registration-service",
                    state="registration_password",
                    retryable=False,
                    status_code=409,
                )
            if self._is_security_verification_error(registration_result):
                raise ServiceError(
                    code="CAPTCHA_TOKEN_INVALID",
                    message="nexos turnstile verification failed",
                    service="registration-service",
                    state="registration_password",
                    retryable=False,
                    status_code=422,
                )
            if not self._is_registration_success(status_code, registration_result):
                raise ServiceError(
                    code="REGISTRATION_SUBMIT_FAILED",
                    message=" ".join(flow_message_texts(registration_result)) or f"unexpected registration status: {status_code}",
                    service="registration-service",
                    state="registration_password",
                    retryable=status_code >= 500,
                    details={"status_code": status_code},
                    status_code=503 if status_code >= 500 else 422,
                )
            self._log(logs, "注册密码步骤已提交，准备触发邮箱验证")

        known_message_ids = {message.id for message in mailbox.list_messages()}
        verification_flow = client.create_verification_flow()
        status_code, verification_step = client.send_verification_code(verification_flow, email=mail_account.address)
        if status_code != 200 or not self._is_verification_sent(verification_step):
            raise ServiceError(
                code="VERIFICATION_SEND_FAILED",
                message=" ".join(flow_message_texts(verification_step)) or f"unexpected verification send status: {status_code}",
                service="registration-service",
                state="verification_send",
                retryable=status_code >= 500,
                details={"status_code": status_code},
                status_code=503 if status_code >= 500 else 422,
            )
        self._log(logs, "验证邮件已发送，等待验证码")

        verification_code = self._wait_for_verification_code(mailbox, known_message_ids, strategy, logs)
        self._cancel_check(strategy)
        status_code, verification_result = client.verify_code(verification_step, code=verification_code)
        if self._is_invalid_verification_code(verification_result):
            raise ServiceError(
                code="VERIFICATION_CODE_INVALID",
                message="verification code is invalid or expired",
                service="registration-service",
                state="verification_code",
                retryable=True,
                status_code=422,
            )
        if not self._is_verification_success(verification_result):
            raise ServiceError(
                code="VERIFICATION_CONFIRM_FAILED",
                message=" ".join(flow_message_texts(verification_result)) or f"unexpected verification confirm status: {status_code}",
                service="registration-service",
                state="verification_code",
                retryable=status_code >= 500,
                details={"status_code": status_code},
                status_code=503 if status_code >= 500 else 422,
            )
        self._log(logs, "邮箱验证成功，准备登录")

        session_handle, result_identity, flags, site_result = self._login_and_load_identity(client, mail_account.address, identity.password)
        if used_browser_flow:
            flags = {**flags, "browser_registration": True}
            site_result = {**site_result, "browser_registration": True, "browser_meta": browser_meta}
        session = RegistrationSession(
            access_token=session_handle,
            refresh_token=None,
            cookies=client.current_cookies(),
            expires_at=None,
        )
        return RegistrationResult(
            site=self.site_name,
            account={"email": mail_account.address, "password": identity.password},
            session=session,
            identity=result_identity,
            flags=flags,
            site_result=site_result,
            artifacts=[{"type": "debug_log", "name": "nexos_register", "meta": {"steps": logs[-50:]}}],
        )
