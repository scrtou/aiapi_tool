from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from DrissionPage import ChromiumOptions, ChromiumPage

from libs.clients.nexos_client import NEXOS_BASE_URL, NexosAuthClient
from libs.contracts.login import LoginCredentials
from libs.contracts.mail import MailAccount
from libs.core.config import env_str
from services.registration_service.mail_client import MailServiceMailboxClient


def _mk_selector(selector: str) -> str:
    if selector.startswith("xpath="):
        return f"xpath:{selector[6:]}"
    if selector.startswith("//"):
        return f"xpath:{selector}"
    return f"css:{selector}"


def _first_ele(page: ChromiumPage, selectors: list[str], timeout: float = 10):
    end = time.time() + timeout
    while time.time() < end:
        for sel in selectors:
            try:
                ele = page.ele(_mk_selector(sel), timeout=1)
                if ele:
                    return ele
            except Exception:
                continue
        time.sleep(0.3)
    return None


def _click(page: ChromiumPage, selectors: list[str], logs: list[str], timeout: float = 10) -> bool:
    ele = _first_ele(page, selectors, timeout=timeout)
    if not ele:
        logs.append(f"click failed, selectors={selectors}")
        return False
    try:
        ele.click()
        return True
    except Exception as exc:
        logs.append(f"click exception: {exc}")
        return False


def _fill(page: ChromiumPage, selectors: list[str], value: str, logs: list[str], timeout: float = 10) -> bool:
    ele = _first_ele(page, selectors, timeout=timeout)
    if not ele:
        logs.append(f"fill failed, selectors={selectors}")
        return False
    try:
        try:
            ele.clear()
        except Exception:
            pass
        ele.input(value)
        return True
    except Exception as exc:
        logs.append(f"fill exception: {exc}")
        return False


def _dismiss_cookie(page: ChromiumPage):
    try:
        btn = page.ele('xpath://button[contains(., "OK") or contains(., "Accept") or contains(., "同意") or contains(., "I agree")]', timeout=2)
        if btn:
            btn.click()
            time.sleep(0.3)
    except Exception:
        pass


def _save_debug(page: ChromiumPage, tag: str, logs: list[str]):
    try:
        temp_dir = Path("/tmp/aiapi_tool_nexos_runner")
        temp_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        png = temp_dir / f"{tag}_{ts}.png"
        html = temp_dir / f"{tag}_{ts}.html"
        page.get_screenshot(path=str(png))
        html.write_text(page.html or "", encoding="utf-8")
        logs.append(f"saved debug: {png}")
    except Exception:
        pass


def _collect_feedback(page: ChromiumPage) -> list[str]:
    try:
        result = page.run_js(
            """
            return Array.from(document.querySelectorAll('[role="alert"], [aria-live], [data-invalid="true"], .text-destructive, .text-red-500, .text-warning, .text-error, p, div, span'))
              .filter(el => el && el.offsetParent !== null)
              .map(el => (el.innerText || el.textContent || '').trim())
              .filter(Boolean)
              .filter(t => t.length <= 260)
              .filter(t => /password|invalid|error|required|weak|verify|activation|email|login|continue|human|strategy/i.test(t))
              .slice(0, 20);
            """
        )
        if isinstance(result, list):
            out = []
            seen = set()
            for item in result:
                if isinstance(item, str) and item.strip() and item not in seen:
                    out.append(item.strip())
                    seen.add(item.strip())
            return out
    except Exception:
        pass
    return []


def _request_submit(page: ChromiumPage, js: str, logs: list[str], note: str):
    try:
        result = page.run_js(js)
        logs.append(f"{note}: requestSubmit result={result}")
    except Exception as exc:
        logs.append(f"{note}: requestSubmit exception: {exc}")


def _get_turnstile_token(page: ChromiumPage) -> str:
    try:
        val = page.run_js("return document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || ''")
        return val if isinstance(val, str) else ""
    except Exception:
        return ""


def _ensure_turnstile(page: ChromiumPage, context: str, logs: list[str], timeout: int = 90) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        token = _get_turnstile_token(page)
        if token and len(token) > 10:
            logs.append(f"{context}: Turnstile token already present")
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
                logs.append(f"{context}: Clicked Turnstile checkbox via required flow")
        except Exception as exc:
            logs.append(f"{context}: Turnstile click flow error: {exc}")

        time.sleep(1.5)
        token = _get_turnstile_token(page)
        if token and len(token) > 10:
            logs.append(f"{context}: Turnstile verified")
            return True

    logs.append(f"{context}: Turnstile not verified in time")
    _save_debug(page, f"turnstile_{context.replace(' ', '_').replace('/', '_')}", logs)
    return False


def _wait_for_registration_submit(page: ChromiumPage, pwd_selectors: list[str], timeout: int = 12) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        current_url = str(page.url or "").lower()
        html = str(page.html or "").lower()
        if any(
            token in current_url or token in html
            for token in ("/verification", "check your email", "activation code", "verification code", "verify your email", "back to login")
        ):
            return True
        code_input = _first_ele(page, ["input[name='code']", "input[type='text']"], timeout=0.8)
        if code_input:
            return True
        pwd_ele = _first_ele(page, pwd_selectors, timeout=0.8)
        if not pwd_ele:
            return True
        time.sleep(0.5)
    return False


def _wait_for_login_submit(page: ChromiumPage, login_pwd_selectors: list[str], timeout: int = 12) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        current_url = str(page.url or "").lower()
        if any(token in current_url for token in ("/chat", "/authorization/consent", "/oauth2/")):
            return True
        pwd_ele = _first_ele(page, login_pwd_selectors, timeout=0.8)
        if not pwd_ele:
            return True
        time.sleep(0.5)
    return False


def _extract_confirmation_link(subject: str, text: str, html: str) -> str | None:
    content = f"{text or ''} {html or ''}"
    links = re.findall(r"https?://[^\s<>\"']+", content, re.IGNORECASE)
    if not links:
        return None
    preferred = [u for u in links if any(host in u.lower() for host in ("nexos.ai", "workspace.nexos.ai", "login.nexos.ai", "url4092.nexos.ai"))]
    if any(k in (subject or "").lower() for k in ("confirm", "verify", "verification", "nexos", "activation")) or preferred:
        return (preferred[0] if preferred else links[0]).replace("&amp;", "&")
    return None


def _extract_verification_code(subject: str, text: str, html: str) -> str | None:
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


def _wait_for_confirmation_link(mailbox: MailServiceMailboxClient, known_ids: set[str], logs: list[str], timeout: int = 120) -> str:
    deadline = time.time() + timeout
    seen_ids = set(known_ids)
    while time.time() < deadline:
        messages = mailbox.list_messages()
        for msg in messages:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            detail = mailbox.get_message(msg.id)
            link = _extract_confirmation_link(detail.subject, detail.text, " ".join(detail.html or []))
            if link:
                logs.append(f"Found confirmation link from subject '{detail.subject}': {link}")
                return link
        time.sleep(5)
    raise RuntimeError("No confirmation link found in mailbox")


def _wait_for_verification_code(mailbox: MailServiceMailboxClient, known_ids: set[str], logs: list[str], timeout: int = 60) -> str | None:
    deadline = time.time() + timeout
    seen_ids = set(known_ids)
    while time.time() < deadline:
        messages = mailbox.list_messages()
        for msg in messages:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            detail = mailbox.get_message(msg.id)
            code = _extract_verification_code(detail.subject, detail.text, " ".join(detail.html or []))
            if code:
                logs.append(f"Found verification code from subject '{detail.subject}': {code}")
                return code
        time.sleep(5)
    return None


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def _build_page(proxy_url: str | None):
    profile_dir = Path(tempfile.mkdtemp(prefix="nexos_xvfb_profile_"))
    co = ChromiumOptions()
    browser_path = env_str("NEXOS_DRISSION_BROWSER_PATH") or "/usr/bin/chromium"
    if Path("/usr/bin/google-chrome").exists():
        browser_path = "/usr/bin/google-chrome"
    co.set_browser_path(browser_path)
    co.headless(False)
    co.incognito(True)
    co.set_local_port(_free_port())
    co.set_user_data_path(str(profile_dir))
    co.set_argument("--window-size=1280,720")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")

    proxy_match = re.match(r"^(https?)://([^:@/]+):([^@/]+)@([^:/]+):(\d+)$", proxy_url or "")
    if proxy_match:
        ext_dir = profile_dir / "proxy_ext"
        ext_dir.mkdir(parents=True, exist_ok=True)
        scheme, username, password, host, port = proxy_match.groups()
        (ext_dir / "manifest.json").write_text(json.dumps({
            "manifest_version": 3,
            "name": "DP Proxy Auth",
            "version": "1.0.0",
            "permissions": ["proxy", "storage", "tabs", "webRequest", "webRequestAuthProvider"],
            "host_permissions": ["<all_urls>"],
            "background": {"service_worker": "background.js"},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (ext_dir / "background.js").write_text(
            """
const config = {
  mode: 'fixed_servers',
  rules: { singleProxy: { scheme: '__SCHEME__', host: '__HOST__', port: __PORT__ }, bypassList: ['localhost', '127.0.0.1'] }
};
chrome.proxy.settings.set({ value: config, scope: 'regular' }, () => {});
chrome.webRequest.onAuthRequired.addListener(
  () => ({ authCredentials: { username: '__USER__', password: '__PASS__' } }),
  { urls: ['<all_urls>'] },
  ['blocking']
);
            """.replace("__SCHEME__", scheme).replace("__HOST__", host).replace("__PORT__", port).replace("__USER__", username).replace("__PASS__", password),
            encoding="utf-8",
        )
        co.add_extension(str(ext_dir))
    elif proxy_url:
        co.set_proxy(proxy_url)

    return ChromiumPage(co), profile_dir


def _cookie_payload(page: ChromiumPage) -> tuple[str, list[dict[str, Any]], dict[str, str], str]:
    cookies = page.cookies()
    cookie_items: list[tuple[str, str]] = []
    cookie_dict: dict[str, str] = {}
    serialized: list[dict[str, Any]] = []
    for item in cookies:
        if isinstance(item, dict):
            serialized.append(item)
            name = item.get("name")
            value = item.get("value")
            if name is not None and value is not None:
                cookie_items.append((str(name), str(value)))
                cookie_dict[str(name)] = str(value)
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_items)
    session_handle = NexosAuthClient.encode_session_handle(None, cookie_header=cookie_header)
    return session_handle, serialized, cookie_dict, cookie_header


def _whoami_from_page(page: ChromiumPage, proxy_url: str | None) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]], dict[str, str], str]:
    session_handle, cookies, cookie_dict, cookie_header = _cookie_payload(page)
    client = NexosAuthClient()
    valid, whoami = client.whoami(session_handle)
    if not valid:
        return None, None, cookies, cookie_dict, cookie_header
    return session_handle, whoami, cookies, cookie_dict, cookie_header


def _submit_login(page: ChromiumPage, logs: list[str]):
    login_pwd_selectors = ["input[name='password']", "input[type='password']", "input[autocomplete='current-password']"]
    btn_selectors = ["button[name='method']", "button:has-text('Sign in')", "button:has-text('Continue')", "form button[type='submit']"]
    _click(page, btn_selectors, logs, timeout=15)
    time.sleep(2)
    if not _wait_for_login_submit(page, login_pwd_selectors, timeout=5):
        page.run_js(
            """
            const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"]');
            const form = (btn && btn.closest('form')) || document.querySelector('form');
            if (form && form.requestSubmit) { if (btn) form.requestSubmit(btn); else form.requestSubmit(); }
            """
        )
        time.sleep(2)
    if not _wait_for_login_submit(page, login_pwd_selectors, timeout=4):
        page.run_js(
            """
            const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"]');
            if (btn) btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            """
        )
        time.sleep(2)


def _perform_login(page: ChromiumPage, email: str, password: str, logs: list[str], proxy_url: str | None) -> dict[str, Any]:
    page.get(f"{NEXOS_BASE_URL.rstrip('/')}/authorization/login")
    time.sleep(2)
    _dismiss_cookie(page)
    email_selectors = [
        "input[name='identifier']",
        "input[type='email']",
        "input[autocomplete='email']",
        "input[name='email']",
        "xpath://input[contains(@name,'identifier') or contains(@name,'email')]",
    ]
    pwd_selectors = ["input[name='password']", "input[type='password']", "input[autocomplete='current-password']"]
    _click(page, email_selectors, logs, timeout=10)
    if not _ensure_turnstile(page, "Login pre-fill", logs):
        raise RuntimeError("Turnstile was not verified on login pre-fill")
    if not _fill(page, email_selectors, email, logs, timeout=15):
        raise RuntimeError("login email input not found")
    if not _fill(page, pwd_selectors, password, logs, timeout=15):
        raise RuntimeError("login password input not found")
    if not _ensure_turnstile(page, "Login pre-submit", logs):
        raise RuntimeError("Turnstile was not verified on login pre-submit")
    _submit_login(page, logs)
    deadline = time.time() + 60
    while time.time() < deadline:
        session_handle, whoami, cookies, cookie_dict, cookie_header = _whoami_from_page(page, proxy_url)
        if session_handle and whoami:
            current_url = str(page.url or "")
            chat_match = re.search(r"/chat/([a-f0-9\\-]+)", current_url, re.I)
            return {
                "session_handle": session_handle,
                "whoami_payload": whoami,
                "cookies": cookies,
                "cookie_dict": cookie_dict,
                "cookie_header": cookie_header,
                "current_url": current_url,
                "chat_id": chat_match.group(1) if chat_match else None,
            }
        time.sleep(1)
    raise RuntimeError(f"login not confirmed: {page.url}")


def run_register_and_login(payload: dict[str, Any]) -> dict[str, Any]:
    logs: list[str] = []
    identity = payload["identity"]
    mail_account = MailAccount.model_validate(payload["mail_account"])
    mailbox = MailServiceMailboxClient(mail_account)
    proxy_url = payload.get("proxy_url")
    page, profile_dir = _build_page(proxy_url)
    try:
        page.get(f"{NEXOS_BASE_URL.rstrip('/')}/authorization/registration")
        time.sleep(2)
        _dismiss_cookie(page)

        email_selectors = [
            "[data-testid=\"auth-input-traits-email\"]",
            "input[name=\"traits.email\"]",
            "input[type='email']",
            "input[autocomplete='email']",
            "xpath://input[contains(@name, 'email')]",
        ]
        email_ele = _first_ele(page, email_selectors, timeout=20)
        if not email_ele:
            page.get(f"{NEXOS_BASE_URL.rstrip('/')}/authorization/login")
            time.sleep(2)
            _dismiss_cookie(page)
            _click(page, ["[data-testid=\"login-page-sign-up-link\"]", "a:has-text('Create')", "a:has-text('Sign up')", "a[href*='registration']"], logs, timeout=12)
            time.sleep(2)
            email_ele = _first_ele(page, email_selectors, timeout=20)
        if not email_ele:
            raise RuntimeError("Registration email input not found on registration page")
        try:
            email_ele.click()
        except Exception:
            _click(page, email_selectors, logs, timeout=10)

        if not _ensure_turnstile(page, "Registration pre-create", logs):
            _save_debug(page, "registration_turnstile_precreate_failed", logs)
            raise RuntimeError("Turnstile was not verified before create account")
        if not _fill(page, email_selectors, mail_account.address, logs, timeout=15):
            _save_debug(page, "registration_email_fill_failed", logs)
            raise RuntimeError("registration email fill failed")

        create_selectors = [
            "[data-testid=\"auth-submit-method\"]",
            "button[name=\"method\"]",
            "button:has-text('Create account')",
            "button:has-text('Create')",
            "form button[type='submit']",
        ]
        _click(page, create_selectors, logs, timeout=15)
        time.sleep(2)
        if not _ensure_turnstile(page, "Registration post-create", logs):
            _save_debug(page, "registration_turnstile_postcreate_failed", logs)
            raise RuntimeError("Turnstile was not verified after create account")

        pwd_selectors = [
            "[data-testid=\"auth-password-password\"]",
            "input[name=\"password\"]",
            "input[type=\"password\"]",
            "input[autocomplete='new-password']",
            "xpath://input[contains(@name, 'password')]",
        ]
        pwd_ele = _first_ele(page, pwd_selectors, timeout=8)
        if not pwd_ele:
            logs.append("Password step not visible after create, retrying create submit via requestSubmit()")
            _request_submit(
                page,
                """
                const btn = document.querySelector('button[name="method"], button[data-testid="auth-submit-method"], form button[type="submit"]');
                const email = document.querySelector('input[name="traits.email"], input[type="email"]');
                const form = (btn && btn.closest('form')) || (email && email.closest('form')) || document.querySelector('form');
                if (form && form.requestSubmit) { if (btn) form.requestSubmit(btn); else form.requestSubmit(); return true; }
                if (btn) { btn.click(); return true; }
                return false;
                """,
                logs,
                "Registration create retry",
            )
            time.sleep(2)
            pwd_ele = _first_ele(page, pwd_selectors, timeout=8)
        if not pwd_ele:
            feedback = " | ".join(_collect_feedback(page))
            if feedback:
                logs.append(f"Password step feedback: {feedback}")
            _save_debug(page, "registration_password_step_missing", logs)
        if not _fill(page, pwd_selectors, identity["password"], logs, timeout=20):
            feedback = " | ".join(_collect_feedback(page))
            if feedback:
                logs.append(f"Password fill feedback: {feedback}")
            _save_debug(page, "registration_password_fill_failed", logs)
            raise RuntimeError("registration password fill failed")
        if not _ensure_turnstile(page, "Registration pre-continue", logs):
            _save_debug(page, "registration_turnstile_precontinue_failed", logs)
            raise RuntimeError("Turnstile was not verified before continue")

        known_ids = {m.id for m in mailbox.list_messages()}
        _click(page, ["[data-testid=\"auth-submit-method\"]", "button[name=\"method\"]", "button:has-text('Continue')", "form button[type='submit']"], logs, timeout=15)
        time.sleep(2)
        if not _wait_for_registration_submit(page, pwd_selectors, timeout=5):
            page.run_js(
                """
                const pwd = document.querySelector('input[name="password"]');
                const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"], form button[type="submit"]');
                const form = (btn && btn.closest('form')) || (pwd ? pwd.closest('form') : document.querySelector('form'));
                if (form && form.requestSubmit) { if (btn) form.requestSubmit(btn); else form.requestSubmit(); }
                """
            )
            time.sleep(2)
        if not _wait_for_registration_submit(page, pwd_selectors, timeout=4):
            page.run_js(
                """
                const btn = document.querySelector('button[name="method"][value="password"], button[data-testid="auth-submit-method"]');
                if (btn) btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                """
            )
            time.sleep(2)
        if not _wait_for_registration_submit(page, pwd_selectors, timeout=8):
            feedback = " | ".join(_collect_feedback(page))
            if feedback:
                logs.append(f"Registration submit feedback: {feedback}")
            _save_debug(page, "registration_submit_failed", logs)
            raise RuntimeError(" | ".join(_collect_feedback(page)) or f"registration did not leave password step: {page.url}")

        logs.append("Waiting for confirmation email...")
        confirmation_link = _wait_for_confirmation_link(mailbox, known_ids, logs, timeout=120)
        page.get(confirmation_link)
        time.sleep(3)
        _click(page, ["button:has-text('Continue')", "button[name='method']", "form button[type='submit']", ".inline-flex"], logs, timeout=12)
        time.sleep(2)

        verified = False
        verification_seen = set(known_ids)
        for _ in range(20):
            html = str(page.html or "").lower()
            if "successfully verified" in html or "your account has been successfully verified" in html:
                verified = True
                break
            code_input = _first_ele(page, ["input[name='code']", "input[type='text']"], timeout=1.5)
            if code_input:
                code = _wait_for_verification_code(mailbox, verification_seen, logs, timeout=45)
                if code:
                    _fill(page, ["input[name='code']", "input[type='text']"], code, logs, timeout=10)
                    _click(page, ["button:has-text('Continue')", "button[name='method']", "form button[type='submit']", ".inline-flex"], logs, timeout=10)
                    time.sleep(2)
            time.sleep(1)
        if not verified:
            _save_debug(page, "verification_confirm_failed", logs)
            raise RuntimeError("verification page did not show successfully verified")

        login_result = _perform_login(page, mail_account.address, identity["password"], logs, proxy_url)
        return {"ok": True, "logs": logs, **login_result}
    except Exception:
        _save_debug(page, "register_and_login_failed", logs)
        raise
    finally:
        try:
            page.quit()
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


def run_login(payload: dict[str, Any]) -> dict[str, Any]:
    logs: list[str] = []
    creds = LoginCredentials.model_validate(payload["credentials"])
    proxy_url = payload.get("proxy_url")
    page, profile_dir = _build_page(proxy_url)
    try:
        result = _perform_login(page, creds.email, creds.password, logs, proxy_url)
        return {"ok": True, "logs": logs, **result}
    except Exception:
        _save_debug(page, "login_failed", logs)
        raise
    finally:
        try:
            page.quit()
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


def main():
    in_path, out_path = sys.argv[1], sys.argv[2]
    payload = json.loads(Path(in_path).read_text(encoding="utf-8"))
    try:
        mode = payload.get("mode")
        if mode == "register_and_login":
            result = run_register_and_login(payload)
        elif mode == "login":
            result = run_login(payload)
        else:
            raise RuntimeError(f"unsupported mode: {mode}")
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        Path(out_path).write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
