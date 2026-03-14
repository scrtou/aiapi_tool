from __future__ import annotations

import json
import os
import subprocess
import socket
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from libs.clients.nexos_client import NEXOS_BASE_URL, NexosAuthClient
from libs.contracts.login import LoginCredentials
from libs.contracts.mail import MailAccount
from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentity
from libs.core.config import env_bool, env_int, env_str
from libs.core.exceptions import ServiceError
from services.registration_service.mail_client import MailServiceMailboxClient


NEXOS_DRISSION_BROWSER_PATH = env_str("NEXOS_DRISSION_BROWSER_PATH")
NEXOS_DRISSION_HEADLESS = env_bool("NEXOS_DRISSION_HEADLESS", True)
NEXOS_DRISSION_WINDOW_WIDTH = env_int("NEXOS_DRISSION_WINDOW_WIDTH", 1280)
NEXOS_DRISSION_WINDOW_HEIGHT = env_int("NEXOS_DRISSION_WINDOW_HEIGHT", 720)
NEXOS_DRISSION_PROXY_URL = env_str("NEXOS_DRISSION_PROXY_URL")
NEXOS_DRISSION_MAIL_WAIT_SECONDS = env_int("NEXOS_DRISSION_MAIL_WAIT_SECONDS", 120)
NEXOS_DRISSION_MAIL_POLL_INTERVAL_SECONDS = env_int("NEXOS_DRISSION_MAIL_POLL_INTERVAL_SECONDS", 5)
NEXOS_DRISSION_TURNSTILE_TIMEOUT_SECONDS = env_int("NEXOS_DRISSION_TURNSTILE_TIMEOUT_SECONDS", 90)
NEXOS_DRISSION_LOGIN_WAIT_SECONDS = env_int("NEXOS_DRISSION_LOGIN_WAIT_SECONDS", 45)
NEXOS_DRISSION_DEBUG_DIR = env_str("NEXOS_DRISSION_DEBUG_DIR", "/tmp/aiapi_tool_nexos")


class NexosDrissionFlow:
    def __init__(self, *, proxy: ProxyLease | None = None):
        self.proxy = proxy

    def _log(self, logs: list[str], message: str):
        logs.append(message)

    def _cancel_check(self, strategy: dict[str, Any] | None):
        if not isinstance(strategy, dict):
            return
        callback = strategy.get("cancel_check")
        if callable(callback):
            callback()

    def _proxy_url(self) -> str | None:
        if NEXOS_DRISSION_PROXY_URL:
            return NEXOS_DRISSION_PROXY_URL
        if not self.proxy:
            return None
        credentials = ""
        if self.proxy.username:
            credentials = self.proxy.username
            if self.proxy.password:
                credentials = f"{credentials}:{self.proxy.password}"
            credentials = f"{credentials}@"
        scheme = self.proxy.scheme or "http"
        return f"{scheme}://{credentials}{self.proxy.host}:{self.proxy.port}"

    def _browser_path(self) -> str:
        for candidate in [
            NEXOS_DRISSION_BROWSER_PATH,
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/opt/google/chrome/chrome",
        ]:
            if candidate and Path(candidate).exists():
                return candidate
        return "/usr/bin/chromium"

    def _free_local_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return int(port)

    def _run_xvfb_helper(self, payload: dict[str, Any]) -> dict[str, Any]:
        in_fd, in_path = tempfile.mkstemp(prefix="nexos_xvfb_in_", suffix=".json")
        out_fd, out_path = tempfile.mkstemp(prefix="nexos_xvfb_out_", suffix=".json")
        os.close(in_fd)
        os.close(out_fd)
        try:
            Path(in_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            cmd = [
                "xvfb-run",
                "-a",
                sys.executable,
                "-m",
                "services.shared.nexos_xvfb_drission_runner",
                in_path,
                out_path,
            ]
            env = dict(os.environ)
            env.setdefault("PYTHONPATH", "/app:/app/libs:/app/services")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=420, env=env)
            result = {}
            if Path(out_path).exists():
                try:
                    result = json.loads(Path(out_path).read_text(encoding="utf-8"))
                except Exception:
                    result = {}
            if proc.returncode != 0 or not result.get("ok"):
                message = (result.get("error") if isinstance(result, dict) else None) or proc.stderr.strip() or proc.stdout.strip() or "xvfb drission runner failed"
                raise ServiceError(
                    code="REGISTRATION_BROWSER_FLOW_FAILED",
                    message=message,
                    service="nexos-browser",
                    state="registration_browser_flow",
                    retryable=False,
                    details={"stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]},
                    status_code=422,
                )
            return result
        finally:
            for path in (in_path, out_path):
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _build_page(self):
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except Exception as exc:
            raise ServiceError(
                code="DRISSION_UNAVAILABLE",
                message=f"DrissionPage is unavailable: {exc}",
                service="nexos-browser",
                state="import_drission",
                retryable=False,
                status_code=422,
            ) from exc

        profile_dir = Path(tempfile.mkdtemp(prefix="nexos_drission_profile_"))
        co = ChromiumOptions()
        co.set_browser_path(self._browser_path())
        co.headless(NEXOS_DRISSION_HEADLESS)
        co.incognito(True)
        co.set_local_port(self._free_local_port())
        co.set_user_data_path(str(profile_dir))
        co.set_argument(f"--window-size={NEXOS_DRISSION_WINDOW_WIDTH},{NEXOS_DRISSION_WINDOW_HEIGHT}")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")

        proxy_url = self._proxy_url()
        proxy_match = None
        if proxy_url:
            proxy_match = re.match(r"^(https?)://([^:@/]+):([^@/]+)@([^:/]+):(\d+)$", proxy_url)
        if proxy_match:
            ext_dir = profile_dir / "proxy_ext"
            ext_dir.mkdir(parents=True, exist_ok=True)
            scheme, username, password, host, port = proxy_match.groups()
            (ext_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "Nexos Proxy Auth",
                        "version": "1.0.0",
                        "permissions": ["proxy", "storage", "tabs", "webRequest", "webRequestAuthProvider"],
                        "host_permissions": ["<all_urls>"],
                        "background": {"service_worker": "background.js"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (ext_dir / "background.js").write_text(
                """
const config = {
  mode: 'fixed_servers',
  rules: {
    singleProxy: { scheme: '__SCHEME__', host: '__HOST__', port: __PORT__ },
    bypassList: ['localhost', '127.0.0.1']
  }
};
chrome.proxy.settings.set({ value: config, scope: 'regular' }, () => {});
chrome.webRequest.onAuthRequired.addListener(
  () => ({ authCredentials: { username: '__USER__', password: '__PASS__' } }),
  { urls: ['<all_urls>'] },
  ['blocking']
);
                """.replace("__SCHEME__", scheme)
                .replace("__HOST__", host)
                .replace("__PORT__", port)
                .replace("__USER__", username)
                .replace("__PASS__", password),
                encoding="utf-8",
            )
            co.add_extension(str(ext_dir))
        elif proxy_url:
            co.set_proxy(proxy_url)

        return ChromiumPage(co), profile_dir

    def _mk_selector(self, selector: str) -> str:
        if selector.startswith("xpath="):
            return f"xpath:{selector[6:]}"
        if selector.startswith("//"):
            return f"xpath:{selector}"
        return f"css:{selector}"

    def _first_ele(self, page, selectors: list[str], timeout: float = 10):
        end = time.time() + timeout
        while time.time() < end:
            for selector in selectors:
                try:
                    ele = page.ele(self._mk_selector(selector), timeout=1)
                    if ele:
                        return ele
                except Exception:
                    continue
            time.sleep(0.3)
        return None

    def _click(self, page, selectors: list[str], logs: list[str], timeout: float = 10) -> bool:
        ele = self._first_ele(page, selectors, timeout=timeout)
        if not ele:
            return False
        try:
            ele.click()
            self._log(logs, f"clicked: {selectors[0]}")
            return True
        except Exception:
            return False

    def _fill(self, page, selectors: list[str], value: str, logs: list[str], timeout: float = 10) -> bool:
        ele = self._first_ele(page, selectors, timeout=timeout)
        if not ele:
            return False
        try:
            try:
                ele.clear()
            except Exception:
                pass
            ele.input(value)
            self._log(logs, f"filled: {selectors[0]}")
            return True
        except Exception:
            return False

    def _dismiss_cookie(self, page):
        try:
            btn = page.ele('xpath://button[contains(., "OK") or contains(., "Accept") or contains(., "同意") or contains(., "I agree")]', timeout=2)
            if btn:
                btn.click()
                time.sleep(0.3)
        except Exception:
            pass

    def _save_debug(self, page, tag: str, logs: list[str]):
        try:
            debug_dir = Path(NEXOS_DRISSION_DEBUG_DIR)
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            png_path = debug_dir / f"{tag}_{ts}.png"
            html_path = debug_dir / f"{tag}_{ts}.html"
            page.get_screenshot(path=str(png_path))
            html_path.write_text(page.html or "", encoding="utf-8")
            self._log(logs, f"saved debug snapshot: {png_path}")
        except Exception:
            pass

    def _collect_feedback(self, page) -> list[str]:
        try:
            result = page.run_js(
                """
                return Array.from(document.querySelectorAll('[role="alert"], [aria-live], .text-destructive, .text-red-500, .text-error, p, div, span'))
                  .filter(el => el && el.offsetParent !== null)
                  .map(el => (el.innerText || el.textContent || '').trim())
                  .filter(Boolean)
                  .filter(t => t.length <= 260)
                  .filter(t => /password|invalid|error|required|weak|verify|activate|activation|email|login|sign in|sign up|continue|strategy|human/i.test(t))
                  .slice(0, 12);
                """
            )
            if isinstance(result, list):
                out: list[str] = []
                seen: set[str] = set()
                for item in result:
                    if not isinstance(item, str):
                        continue
                    text = item.strip()
                    if not text or text in seen:
                        continue
                    seen.add(text)
                    out.append(text)
                return out
        except Exception:
            pass
        return []

    def _get_turnstile_token(self, page) -> str:
        try:
            value = page.run_js("return document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || ''")
            return value if isinstance(value, str) else ""
        except Exception:
            return ""

    def _ensure_turnstile(self, page, context: str, logs: list[str], strategy: dict[str, Any] | None, timeout: int = NEXOS_DRISSION_TURNSTILE_TIMEOUT_SECONDS) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self._cancel_check(strategy)
            token = self._get_turnstile_token(page)
            if token and len(token) > 10:
                self._log(logs, f"{context}: Turnstile token already present")
                return True

            ts_input = page.ele('@name=cf-turnstile-response', timeout=2)
            if not ts_input:
                return True

            try:
                parent = ts_input.parent()
                iframe = parent.sr('tag:iframe') if parent else None
                body = iframe.ele('tag:body') if iframe else None
                checkbox = body.sr('@type=checkbox') if body else None
                if checkbox:
                    checkbox.click()
                    self._log(logs, f"{context}: Clicked Turnstile checkbox via required flow")
            except Exception:
                pass

            time.sleep(1.5)
            token = self._get_turnstile_token(page)
            if token and len(token) > 10:
                self._log(logs, f"{context}: Turnstile verified")
                return True

        self._log(logs, f"{context}: Turnstile not verified in time")
        self._save_debug(page, f"turnstile_{context.replace(' ', '_').replace('/', '_')}", logs)
        return False

    def _wait_for_registration_submit(self, page, pwd_selectors: list[str], timeout: int = 10) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            current_url = str(getattr(page, "url", "") or "").lower()
            html = str(getattr(page, "html", "") or "").lower()
            if any(
                token in current_url or token in html
                for token in (
                    "/verification",
                    "check your email",
                    "activation code",
                    "verification code",
                    "verify your email",
                    "back to login",
                )
            ):
                return True
            try:
                code_input = self._first_ele(page, ["input[name='code']", "input[type='text']"], timeout=0.8)
                if code_input:
                    return True
            except Exception:
                pass
            try:
                pwd_ele = self._first_ele(page, pwd_selectors, timeout=0.8)
                if not pwd_ele:
                    return True
            except Exception:
                return True
            time.sleep(0.5)
        return False

    def _wait_for_login_submit(self, page, login_pwd_selectors: list[str], timeout: int = 10) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            current_url = str(getattr(page, "url", "") or "").lower()
            if any(token in current_url for token in ("/chat", "/authorization/consent", "/oauth2/")):
                return True
            try:
                pwd_ele = self._first_ele(page, login_pwd_selectors, timeout=0.8)
                if not pwd_ele:
                    return True
            except Exception:
                return True
            time.sleep(0.5)
        return False

    def _cookie_items(self, page) -> list[tuple[str, str]]:
        cookies = page.cookies()
        cookie_items: list[tuple[str, str]] = []
        for cookie in cookies:
            name = cookie.get("name") if isinstance(cookie, dict) else getattr(cookie, "name", None)
            value = cookie.get("value") if isinstance(cookie, dict) else getattr(cookie, "value", None)
            if name is not None and value is not None:
                cookie_items.append((str(name), str(value)))
        return cookie_items

    def _cookie_payloads(self, page) -> tuple[list[dict[str, Any]], str, dict[str, str]]:
        cookies = page.cookies()
        cookie_items = self._cookie_items(page)
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_items)
        cookie_dict = {k: v for k, v in cookie_items}
        serialized: list[dict[str, Any]] = []
        for cookie in cookies:
            if isinstance(cookie, dict):
                serialized.append(cookie)
        return serialized, cookie_header, cookie_dict

    def _whoami_from_page(self, page) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]], str, dict[str, str]]:
        cookies, cookie_header, cookie_dict = self._cookie_payloads(page)
        if not cookie_header:
            return None, None, cookies, cookie_header, cookie_dict
        session_handle = NexosAuthClient.encode_session_handle(None, cookie_header=cookie_header)
        client = NexosAuthClient(proxy=self.proxy)
        valid, whoami_payload = client.whoami(session_handle)
        if not valid:
            return None, None, cookies, cookie_header, cookie_dict
        return session_handle, whoami_payload, cookies, cookie_header, cookie_dict

    def _extract_chat_id(self, url: str) -> str | None:
        match = re.search(r"/chat/([a-f0-9\\-]+)", url or "", re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_confirmation_link(self, subject_raw: str, text_content: str, html_content: str) -> str | None:
        subject = (subject_raw or "").lower()
        content = f"{text_content or ''} {html_content or ''}"
        links = re.findall(r"https?://[^\\s<>\"']+", content, re.IGNORECASE)
        if not links:
            return None
        preferred = [
            link for link in links
            if any(host in link.lower() for host in ("nexos.ai", "workspace.nexos.ai", "login.nexos.ai", "url4092.nexos.ai"))
        ]
        if any(key in subject for key in ("confirm", "verify", "verification", "nexos", "activation")) or preferred:
            return (preferred[0] if preferred else links[0]).replace("&amp;", "&")
        return None

    def _extract_verification_code(self, subject: str, text: str, html: str) -> str | None:
        normalized = re.sub(r"<[^>]+>", " ", " ".join([subject or "", text or "", html or ""]))
        normalized = re.sub(r"\s+", " ", normalized)
        for pattern in [
            r"(?:verification|verify|confirmation|confirm|activation)[^\d]{0,40}(\d{6,8})",
            r"(?:code|token|otp|passcode)[^\d]{0,20}(\d{6,8})",
            r"(?<!\d)(\d{6})(?!\d)",
        ]:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                return str(match.group(1))
        return None

    def _wait_for_confirmation_link(self, mailbox: MailServiceMailboxClient, known_ids: set[str], logs: list[str], strategy: dict[str, Any] | None) -> str:
        deadline = time.time() + NEXOS_DRISSION_MAIL_WAIT_SECONDS
        seen_ids = set(known_ids)
        while time.time() < deadline:
            self._cancel_check(strategy)
            messages = mailbox.list_messages()
            for message in messages:
                if message.id in seen_ids:
                    continue
                seen_ids.add(message.id)
                detail = mailbox.get_message(message.id)
                link = self._extract_confirmation_link(detail.subject, detail.text, " ".join(detail.html or []))
                if link:
                    self._log(logs, f"confirmation link found from subject '{detail.subject}': {link}")
                    return link
            time.sleep(NEXOS_DRISSION_MAIL_POLL_INTERVAL_SECONDS)
        raise ServiceError(
            code="VERIFICATION_EMAIL_TIMEOUT",
            message="timed out waiting for nexos confirmation email",
            service="nexos-browser",
            state="confirmation_email",
            retryable=True,
            status_code=504,
        )

    def _wait_for_verification_code(self, mailbox: MailServiceMailboxClient, known_ids: set[str], logs: list[str], strategy: dict[str, Any] | None, timeout: int = 60) -> str | None:
        deadline = time.time() + timeout
        seen_ids = set(known_ids)
        while time.time() < deadline:
            self._cancel_check(strategy)
            messages = mailbox.list_messages()
            for message in messages:
                if message.id in seen_ids:
                    continue
                seen_ids.add(message.id)
                detail = mailbox.get_message(message.id)
                code = self._extract_verification_code(detail.subject, detail.text, " ".join(detail.html or []))
                if code:
                    self._log(logs, f"verification code found from subject '{detail.subject}': {code}")
                    return code
            time.sleep(5)
        return None

    def _submit_registration_password(self, page, logs: list[str]) -> None:
        continue_selectors = [
            "[data-testid=\"auth-submit-method\"]",
            "button[name=\"method\"]",
            "button:has-text('Continue')",
            "form button[type='submit']",
        ]
        self._log(logs, "Submitting registration password step...")
        self._click(page, continue_selectors, logs, timeout=15)
        time.sleep(2)

        pwd_selectors = [
            "[data-testid=\"auth-password-password\"]",
            "input[name=\"password\"]",
            "input[type=\"password\"]",
        ]
        if not self._wait_for_registration_submit(page, pwd_selectors, timeout=5):
            self._log(logs, "First registration submit attempt did not leave password step, retrying requestSubmit()...")
            try:
                page.run_js(
                    """
                    const pwd = document.querySelector('input[name="password"]');
                    const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"], form button[type="submit"]');
                    const form = (btn && btn.closest('form')) || (pwd ? pwd.closest('form') : document.querySelector('form'));
                    if (form && form.requestSubmit) {
                      if (btn) form.requestSubmit(btn); else form.requestSubmit();
                      return true;
                    }
                    if (btn) { btn.click(); return true; }
                    return false;
                    """
                )
            except Exception:
                pass
            time.sleep(2)

        if not self._wait_for_registration_submit(page, pwd_selectors, timeout=4):
            self._log(logs, "Second registration submit attempt did not leave password step, retrying explicit button click...")
            try:
                page.run_js(
                    """
                    const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"]');
                    if (btn) {
                      btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                      return true;
                    }
                    return false;
                    """
                )
            except Exception:
                pass
            time.sleep(2)

        if not self._wait_for_registration_submit(page, pwd_selectors, timeout=8):
            feedback = self._collect_feedback(page)
            raise ServiceError(
                code="REGISTRATION_BROWSER_SUBMIT_FAILED",
                message=" | ".join(feedback) or f"registration did not leave password step: {page.url}",
                service="nexos-browser",
                state="registration_submit",
                retryable=False,
                details={"url": page.url},
                status_code=422,
            )

    def _submit_login(self, page, logs: list[str]) -> None:
        login_pwd_selectors = [
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='current-password']",
        ]
        login_button_selectors = [
            "button[name='method']",
            "button:has-text('Sign in')",
            "button:has-text('Continue')",
            "form button[type='submit']",
        ]
        self._click(page, login_button_selectors, logs, timeout=15)
        time.sleep(2)

        if not self._wait_for_login_submit(page, login_pwd_selectors, timeout=5):
            self._log(logs, "First login submit attempt did not leave login page, retrying requestSubmit()...")
            try:
                page.run_js(
                    """
                    const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"]');
                    const form = (btn && btn.closest('form')) || document.querySelector('form');
                    if (form && form.requestSubmit) {
                      if (btn) form.requestSubmit(btn); else form.requestSubmit();
                      return true;
                    }
                    if (btn) { btn.click(); return true; }
                    return false;
                    """
                )
            except Exception:
                pass
            time.sleep(2)

        if not self._wait_for_login_submit(page, login_pwd_selectors, timeout=4):
            self._log(logs, "Second login submit attempt did not leave login page, retrying explicit button click...")
            try:
                page.run_js(
                    """
                    const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"]');
                    if (btn) {
                      btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                      return true;
                    }
                    return false;
                    """
                )
            except Exception:
                pass
            time.sleep(2)

    def _wait_for_login_success(self, page, logs: list[str], timeout: int = NEXOS_DRISSION_LOGIN_WAIT_SECONDS) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            current_url = str(getattr(page, "url", "") or "")
            session_handle, whoami_payload, cookies, cookie_header, cookie_dict = self._whoami_from_page(page)
            if session_handle and whoami_payload:
                self._log(logs, f"login session verified via whoami, url={current_url}")
                return {
                    "session_handle": session_handle,
                    "whoami_payload": whoami_payload,
                    "cookies": cookies,
                    "cookie_header": cookie_header,
                    "cookie_dict": cookie_dict,
                    "current_url": current_url,
                    "chat_id": self._extract_chat_id(current_url),
                }
            if "/chat" in current_url.lower():
                session_handle, whoami_payload, cookies, cookie_header, cookie_dict = self._whoami_from_page(page)
                if session_handle and whoami_payload:
                    return {
                        "session_handle": session_handle,
                        "whoami_payload": whoami_payload,
                        "cookies": cookies,
                        "cookie_header": cookie_header,
                        "cookie_dict": cookie_dict,
                        "current_url": current_url,
                        "chat_id": self._extract_chat_id(current_url),
                    }
            time.sleep(1)
        feedback = self._collect_feedback(page)
        raise ServiceError(
            code="LOGIN_BROWSER_FAILED",
            message=" | ".join(feedback) or f"login not confirmed: {page.url}",
            service="nexos-browser",
            state="login_submit",
            retryable=False,
            details={"url": page.url},
            status_code=422,
        )

    def _perform_login(self, page, email: str, password: str, logs: list[str], strategy: dict[str, Any] | None) -> dict[str, Any]:
        page.get(f"{NEXOS_BASE_URL.rstrip('/')}/authorization/login")
        time.sleep(2)
        self._dismiss_cookie(page)

        email_selectors = [
            "input[name='identifier']",
            "input[type='email']",
            "input[autocomplete='email']",
            "input[name='email']",
            "xpath://input[contains(@name,'identifier') or contains(@name,'email')]",
        ]
        password_selectors = [
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='current-password']",
        ]

        self._click(page, email_selectors, logs, timeout=10)
        if not self._ensure_turnstile(page, "Login pre-fill", logs, strategy):
            raise ServiceError(
                code="TURNSTILE_NOT_VERIFIED",
                message="Turnstile was not verified on login pre-fill",
                service="nexos-browser",
                state="login_turnstile_prefill",
                retryable=False,
                status_code=422,
            )

        if not self._fill(page, email_selectors, email, logs, timeout=15):
            raise ServiceError(
                code="LOGIN_EMAIL_INPUT_MISSING",
                message="login email input not found",
                service="nexos-browser",
                state="login_email",
                retryable=False,
                status_code=422,
            )
        if not self._fill(page, password_selectors, password, logs, timeout=15):
            raise ServiceError(
                code="LOGIN_PASSWORD_INPUT_MISSING",
                message="login password input not found",
                service="nexos-browser",
                state="login_password",
                retryable=False,
                status_code=422,
            )

        if not self._ensure_turnstile(page, "Login pre-submit", logs, strategy):
            raise ServiceError(
                code="TURNSTILE_NOT_VERIFIED",
                message="Turnstile was not verified on login pre-submit",
                service="nexos-browser",
                state="login_turnstile_presubmit",
                retryable=False,
                status_code=422,
            )

        self._submit_login(page, logs)
        return self._wait_for_login_success(page, logs)

    def register_and_login(
        self,
        identity: RegistrationIdentity,
        mail_account: MailAccount,
        mailbox: MailServiceMailboxClient,
        strategy: dict[str, Any] | None,
        logs: list[str],
    ) -> dict[str, Any]:
        payload = {
            "mode": "register_and_login",
            "identity": identity.model_dump(mode="json") if hasattr(identity, "model_dump") else dict(identity),
            "mail_account": mail_account.model_dump(mode="json"),
            "proxy_url": self._proxy_url(),
        }
        result = self._run_xvfb_helper(payload)
        logs.extend(result.get("logs") or [])
        return result

    def login(self, credentials: LoginCredentials, strategy: dict[str, Any] | None, logs: list[str]) -> dict[str, Any]:
        payload = {
            "mode": "login",
            "credentials": credentials.model_dump(mode="json"),
            "proxy_url": self._proxy_url(),
        }
        result = self._run_xvfb_helper(payload)
        logs.extend(result.get("logs") or [])
        return result

    def _register_and_login_legacy(
        self,
        identity: RegistrationIdentity,
        mail_account: MailAccount,
        mailbox: MailServiceMailboxClient,
        strategy: dict[str, Any] | None,
        logs: list[str],
    ) -> dict[str, Any]:
        page = None
        profile_dir: Path | None = None
        try:
            page, profile_dir = self._build_page()
            registration_url = f"{NEXOS_BASE_URL.rstrip('/')}/authorization/registration"
            page.get(registration_url)
            time.sleep(2)
            self._dismiss_cookie(page)

            email_selectors = [
                "[data-testid=\"auth-input-traits-email\"]",
                "input[name=\"traits.email\"]",
                "input[type='email']",
                "input[autocomplete='email']",
                "xpath://input[contains(@name, 'email')]",
            ]
            email_ele = self._first_ele(page, email_selectors, timeout=20)
            if not email_ele:
                page.get(f"{NEXOS_BASE_URL.rstrip('/')}/authorization/login")
                time.sleep(2)
                self._dismiss_cookie(page)
                self._click(
                    page,
                    [
                        "[data-testid=\"login-page-sign-up-link\"]",
                        "a:has-text('Create')",
                        "a:has-text('Sign up')",
                        "a[href*='registration']",
                    ],
                    logs,
                    timeout=12,
                )
                time.sleep(2)
                email_ele = self._first_ele(page, email_selectors, timeout=20)
            if not email_ele:
                raise ServiceError(
                    code="REGISTRATION_EMAIL_INPUT_MISSING",
                    message="registration email input not found",
                    service="nexos-browser",
                    state="registration_email",
                    retryable=False,
                    status_code=422,
                )

            try:
                email_ele.click()
            except Exception:
                self._click(page, email_selectors, logs, timeout=10)

            if not self._ensure_turnstile(page, "Registration pre-create", logs, strategy):
                raise ServiceError(
                    code="TURNSTILE_NOT_VERIFIED",
                    message="Turnstile was not verified before create account",
                    service="nexos-browser",
                    state="registration_turnstile_precreate",
                    retryable=False,
                    status_code=422,
                )

            if not self._fill(page, email_selectors, mail_account.address, logs, timeout=15):
                raise ServiceError(
                    code="REGISTRATION_EMAIL_FILL_FAILED",
                    message="registration email fill failed",
                    service="nexos-browser",
                    state="registration_email_fill",
                    retryable=False,
                    status_code=422,
                )

            self._click(
                page,
                [
                    "[data-testid=\"auth-submit-method\"]",
                    "button[name=\"method\"]",
                    "button:has-text('Create account')",
                    "button:has-text('Create')",
                    "form button[type='submit']",
                ],
                logs,
                timeout=15,
            )
            time.sleep(2)

            if not self._ensure_turnstile(page, "Registration post-create", logs, strategy):
                raise ServiceError(
                    code="TURNSTILE_NOT_VERIFIED",
                    message="Turnstile was not verified after create account",
                    service="nexos-browser",
                    state="registration_turnstile_postcreate",
                    retryable=False,
                    status_code=422,
                )

            password_selectors = [
                "[data-testid=\"auth-password-password\"]",
                "input[name=\"password\"]",
                "input[type=\"password\"]",
            ]
            if not self._fill(page, password_selectors, identity.password, logs, timeout=20):
                raise ServiceError(
                    code="REGISTRATION_PASSWORD_FILL_FAILED",
                    message="registration password fill failed",
                    service="nexos-browser",
                    state="registration_password_fill",
                    retryable=False,
                    status_code=422,
                )

            if not self._ensure_turnstile(page, "Registration pre-continue", logs, strategy):
                raise ServiceError(
                    code="TURNSTILE_NOT_VERIFIED",
                    message="Turnstile was not verified before registration continue",
                    service="nexos-browser",
                    state="registration_turnstile_precontinue",
                    retryable=False,
                    status_code=422,
                )

            known_message_ids = {message.id for message in mailbox.list_messages()}
            self._submit_registration_password(page, logs)

            confirmation_link = self._wait_for_confirmation_link(mailbox, known_message_ids, logs, strategy)
            page.get(confirmation_link)
            time.sleep(3)

            self._click(
                page,
                [
                    "button:has-text('Continue')",
                    "button[name='method']",
                    "form button[type='submit']",
                    ".inline-flex",
                ],
                logs,
                timeout=12,
            )
            time.sleep(2)

            verified = False
            verification_seen_ids = set(known_message_ids)
            for _ in range(20):
                html = str(getattr(page, "html", "") or "").lower()
                if "successfully verified" in html or "your account has been successfully verified" in html:
                    verified = True
                    break
                code_input = self._first_ele(page, ["input[name='code']", "input[type='text']"], timeout=1.5)
                if code_input:
                    code = self._wait_for_verification_code(mailbox, verification_seen_ids, logs, strategy, timeout=45)
                    if code:
                        self._fill(page, ["input[name='code']", "input[type='text']"], code, logs, timeout=10)
                        self._click(
                            page,
                            ["button:has-text('Continue')", "button[name='method']", "form button[type='submit']", ".inline-flex"],
                            logs,
                            timeout=10,
                        )
                        time.sleep(2)
                time.sleep(1)

            if not verified:
                raise ServiceError(
                    code="VERIFICATION_CONFIRM_FAILED",
                    message="verification page did not show successfully verified",
                    service="nexos-browser",
                    state="verification_confirm",
                    retryable=False,
                    status_code=422,
                )
            self._log(logs, "Account verification page confirmed: successfully verified")

            login_result = self._perform_login(page, mail_account.address, identity.password, logs, strategy)
            return {
                **login_result,
                "email": mail_account.address,
                "password": identity.password,
                "browser_meta": {
                    "current_url": login_result.get("current_url"),
                    "chat_id": login_result.get("chat_id"),
                    "cookie_count": len(login_result.get("cookies") or []),
                },
            }
        except ServiceError:
            if page is not None:
                self._save_debug(page, "registration_browser_failed", logs)
            raise
        except Exception as exc:
            if page is not None:
                self._save_debug(page, "registration_browser_failed", logs)
            raise ServiceError(
                code="REGISTRATION_BROWSER_FLOW_FAILED",
                message=str(exc),
                service="nexos-browser",
                state="registration_browser_flow",
                retryable=False,
                status_code=422,
            ) from exc
        finally:
            if page is not None:
                try:
                    page.quit()
                except Exception:
                    pass
            if profile_dir:
                shutil.rmtree(profile_dir, ignore_errors=True)

    def _login_legacy(self, credentials: LoginCredentials, strategy: dict[str, Any] | None, logs: list[str]) -> dict[str, Any]:
        page = None
        profile_dir: Path | None = None
        try:
            page, profile_dir = self._build_page()
            return self._perform_login(page, credentials.email, credentials.password, logs, strategy)
        except ServiceError:
            if page is not None:
                self._save_debug(page, "login_browser_failed", logs)
            raise
        except Exception as exc:
            if page is not None:
                self._save_debug(page, "login_browser_failed", logs)
            raise ServiceError(
                code="LOGIN_BROWSER_FAILED",
                message=str(exc),
                service="nexos-browser",
                state="login_browser_flow",
                retryable=False,
                status_code=422,
            ) from exc
        finally:
            if page is not None:
                try:
                    page.quit()
                except Exception:
                    pass
            if profile_dir:
                shutil.rmtree(profile_dir, ignore_errors=True)
