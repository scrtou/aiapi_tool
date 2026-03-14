"""
SmailPro 网页版自动化客户端

通过 Selenium 驱动 https://smailpro.com/temporary-email 页面，
调用页面自身的创建/收件逻辑，避免直接依赖付费 API key。
"""

import json
import os
import random
import re
import secrets
import shutil
import string
import tempfile
import time
from typing import Optional, List, Dict, Any

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

try:
    from libs.clients.duckmail_client import (
        DuckMailAccount,
        EmailMessage,
        EmailDetail,
        DEFAULT_SUBJECT_PATTERNS,
        VERIFICATION_SENDERS,
        log_message,
    )
except ImportError:
    from libs.clients.duckmail_client import (
        DuckMailAccount,
        EmailMessage,
        EmailDetail,
        DEFAULT_SUBJECT_PATTERNS,
        VERIFICATION_SENDERS,
        log_message,
    )

try:
    from libs.core.tracing import get_current_trace_id
except ImportError:
    def get_current_trace_id():
        return None


SMAILPRO_WEB_URL = os.getenv("SMAILPRO_WEB_URL", "https://smailpro.com/temporary-email")
SMAILPRO_WEB_PATTERN = os.getenv("SMAILPRO_WEB_PATTERN", "random@gmail.com-1")
SMAILPRO_WEB_HEADLESS = os.getenv("SMAILPRO_WEB_HEADLESS", "0") == "1"
SMAILPRO_WEB_AUTO_VISIBLE_FALLBACK = os.getenv("SMAILPRO_WEB_AUTO_VISIBLE_FALLBACK", "1") == "1"
SMAILPRO_WEB_TIMEOUT = int(os.getenv("SMAILPRO_WEB_TIMEOUT", "60"))
SMAILPRO_WEB_XVFB_WRAPPER = os.getenv("SMAILPRO_WEB_XVFB_WRAPPER", "/tmp/smailpro-chromium-xvfb-wrapper.sh")
SMAILPRO_WEB_PROFILE_DIR = os.getenv("SMAILPRO_WEB_PROFILE_DIR", os.path.expanduser("~/.cache/aiapi_tool/smailpro_web_profile"))
SMAILPRO_WEB_SHARED_PROFILE = os.getenv("SMAILPRO_WEB_SHARED_PROFILE", "0") == "1"
SMAILPRO_WEB_MIN_HUMAN_DELAY = float(os.getenv("SMAILPRO_WEB_MIN_HUMAN_DELAY", "1.2"))
SMAILPRO_WEB_MAX_HUMAN_DELAY = float(os.getenv("SMAILPRO_WEB_MAX_HUMAN_DELAY", "3.0"))

_GOOGLE_DOMAINS = {"gmail.com", "googlemail.com"}
_MICROSOFT_DOMAINS = {
    "outlook.com",
    "hotmail.com",
    "outlook.kr",
    "outlook.fr",
    "outlook.com.vn",
    "outlook.co.id",
    "outlook.co.th",
    "outlook.com.ar",
    "outlook.co.il",
}


class SmailProWebClient:
    """SmailPro 网页版自动化邮箱客户端"""

    def __init__(self, driver: Optional[webdriver.Chrome] = None, headless: Optional[bool] = None):
        self.driver = driver
        self.owns_driver = driver is None
        self.headless = SMAILPRO_WEB_HEADLESS if headless is None else bool(headless)
        self.temp_email_url = SMAILPRO_WEB_URL
        self.account: Optional[DuckMailAccount] = None
        self.account_meta: Optional[Dict[str, Any]] = None
        self.window_handle: Optional[str] = None
        self.parent_window_handle: Optional[str] = None
        self.profile_dir: Optional[str] = None

    def _ensure_profile_dir(self) -> Optional[str]:
        if not self.owns_driver:
            return None
        if self.profile_dir:
            return self.profile_dir

        base_dir = os.path.expanduser(SMAILPRO_WEB_PROFILE_DIR)
        os.makedirs(base_dir, exist_ok=True)

        if SMAILPRO_WEB_SHARED_PROFILE:
            self.profile_dir = base_dir
        else:
            sessions_dir = os.path.join(base_dir, "sessions")
            os.makedirs(sessions_dir, exist_ok=True)
            self.profile_dir = tempfile.mkdtemp(prefix="smailpro-", dir=sessions_dir)

        return self.profile_dir

    def _log(self, message: str):
        trace_id = get_current_trace_id()
        if trace_id:
            log_message(f"[trace_id={trace_id}] {message}")
        else:
            log_message(message)

    @staticmethod
    def generate_email_prefix(length: int = 10) -> str:
        chars = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def generate_password(length: int = 16) -> str:
        chars = string.ascii_letters + string.digits + "!@#$%"
        return ''.join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def _ensure_xvfb_wrapper() -> str:
        wrapper_path = SMAILPRO_WEB_XVFB_WRAPPER
        chromium_binary = '/usr/bin/chromium' if os.path.exists('/usr/bin/chromium') else '/tmp/cft/chrome-linux64/chrome'
        script = f"#!/usr/bin/env bash\nexec xvfb-run -a {chromium_binary} \"$@\"\n"
        current = None
        if os.path.exists(wrapper_path):
            try:
                with open(wrapper_path, 'r', encoding='utf-8') as f:
                    current = f.read()
            except Exception:
                current = None
        if current != script:
            with open(wrapper_path, 'w', encoding='utf-8') as f:
                f.write(script)
            os.chmod(wrapper_path, 0o755)
        return wrapper_path

    def _get_chrome_options(self) -> Options:
        options = Options()
        if self.headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1440,1200')
        options.add_argument('--lang=en-US')
        options.add_argument('--disable-features=AutomationControlled')
        options.add_argument('--start-maximized')
        profile_dir = self._ensure_profile_dir()
        if profile_dir:
            options.add_argument(f'--user-data-dir={profile_dir}')
            if SMAILPRO_WEB_SHARED_PROFILE:
                options.add_argument('--profile-directory=Default')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--disable-notifications')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        chrome_binary = os.getenv("CHROME_BINARY")
        if chrome_binary:
            options.binary_location = chrome_binary
        elif not self.headless:
            options.binary_location = SmailProWebClient._ensure_xvfb_wrapper()
        return options

    def _get_chrome_service(self) -> Service:
        if not self.headless and os.path.exists('/usr/bin/chromedriver'):
            return Service('/usr/bin/chromedriver')
        chromedriver = os.getenv("CHROMEDRIVER_PATH")
        if chromedriver and os.path.exists(chromedriver):
            return Service(chromedriver)
        if os.path.exists('/usr/bin/chromedriver'):
            return Service('/usr/bin/chromedriver')
        if os.path.exists('/usr/local/bin/chromedriver'):
            return Service('/usr/local/bin/chromedriver')
        return Service(ChromeDriverManager().install())

    def _ensure_driver(self):
        if self.driver:
            return
        self._ensure_profile_dir()
        self.driver = webdriver.Chrome(service=self._get_chrome_service(), options=self._get_chrome_options())
        self.driver.implicitly_wait(5)
        self._apply_stealth()

    def _apply_stealth(self):
        if not self.driver:
            return
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                        window.chrome = window.chrome || { runtime: {} };
                    """
                },
            )
        except Exception:
            pass

    @staticmethod
    def _human_delay(scale: float = 1.0):
        time.sleep(random.uniform(SMAILPRO_WEB_MIN_HUMAN_DELAY, SMAILPRO_WEB_MAX_HUMAN_DELAY) * scale)

    def _humanize_page(self):
        if not self.driver:
            return
        try:
            height = self.driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 1200)")
            scroll_points = [0, min(300, height), min(700, height), min(height - 200, max(0, height - 200)), 0]
            for point in scroll_points:
                self.driver.execute_script("window.scrollTo({top: arguments[0], behavior: 'instant'});", point)
                self._human_delay(0.4)

            self.driver.execute_script(
                """
                const eventInit = {bubbles:true, cancelable:true, clientX: 200, clientY: 250};
                document.dispatchEvent(new MouseEvent('mousemove', eventInit));
                document.dispatchEvent(new MouseEvent('mouseover', eventInit));
                document.dispatchEvent(new MouseEvent('mouseenter', eventInit));
                """
            )
            self._human_delay(0.6)
        except Exception:
            pass

    def _ensure_window(self):
        self._ensure_driver()
        if self.window_handle:
            return
        if self.owns_driver:
            self.window_handle = self.driver.current_window_handle
            return
        self.parent_window_handle = self.driver.current_window_handle
        self.driver.execute_script("window.open('about:blank', '_blank');")
        self.window_handle = self.driver.window_handles[-1]

    def _switch_to_window(self):
        self._ensure_window()
        self.driver.switch_to.window(self.window_handle)

    def _ensure_page(self):
        self._switch_to_window()
        if not self.driver.current_url.startswith(self.temp_email_url):
            self.driver.get(self.temp_email_url)
        WebDriverWait(self.driver, SMAILPRO_WEB_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, "//div[@x-data='TemporaryEmail()']"))
        )
        self._human_delay(1.0)
        self._humanize_page()

    @staticmethod
    def _parse_pattern(pattern: str) -> Dict[str, str]:
        pattern = (pattern or SMAILPRO_WEB_PATTERN).strip().lower()
        server = "1"
        main_part = pattern
        if '-' in pattern:
            parts = pattern.split('-')
            server = parts.pop() or "1"
            main_part = '-'.join(parts)

        if '@' not in main_part:
            raise ValueError(f"无效的邮箱模式: {pattern}")

        before_domain, domain = main_part.split('@', 1)
        username = before_domain
        account_type = "alias"
        matches = re.match(r'^([^\[]+)\[(.*?)\]$', before_domain)
        if matches:
            username = matches.group(1)
            account_type = matches.group(2) or "alias"

        return {
            "username": username,
            "type": account_type,
            "domain": domain,
            "server": server,
        }

    @staticmethod
    def _provider_for_email(email: str) -> str:
        domain = email.split('@', 1)[1].lower()
        if domain in _GOOGLE_DOMAINS:
            return "google"
        if domain in _MICROSOFT_DOMAINS:
            return "microsoft"
        return "other"

    def _execute_async(self, script: str, *args, timeout: int = SMAILPRO_WEB_TIMEOUT):
        self._switch_to_window()
        self.driver.set_script_timeout(timeout)
        return self.driver.execute_async_script(script, *args)

    @staticmethod
    def _extract_balanced_block(source: str, start_index: int, open_char: str = "{", close_char: str = "}") -> str:
        if start_index < 0 or start_index >= len(source) or source[start_index] != open_char:
            raise ValueError(f"invalid block start index for {open_char}{close_char}")

        depth = 0
        quote = None
        escaped = False

        for idx in range(start_index, len(source)):
            ch = source[idx]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue

            if ch in ("'", '"'):
                quote = ch
                continue
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return source[start_index:idx + 1]

        raise ValueError(f"unterminated block for {open_char}{close_char}")

    @classmethod
    def _extract_settings_block(cls, html: str) -> str:
        marker = "settings:"
        marker_index = html.find(marker)
        if marker_index < 0:
            raise ValueError("unable to find SmailPro settings block")

        block_start = html.find("{", marker_index)
        if block_start < 0:
            raise ValueError("unable to locate settings object start")

        return cls._extract_balanced_block(html, block_start, "{", "}")

    @classmethod
    def _extract_named_section(cls, settings_block: str, section_name: str) -> str:
        pattern = re.compile(rf"\b{re.escape(section_name)}\s*:\s*\{{", re.DOTALL)
        matched = pattern.search(settings_block)
        if not matched:
            raise ValueError(f"missing settings section: {section_name}")

        section_start = settings_block.find("{", matched.start())
        return cls._extract_balanced_block(settings_block, section_start, "{", "}")

    @staticmethod
    def _extract_domain_names(section_block: str, *, object_items: bool = False) -> list[str]:
        domain_match = re.search(r"\bdomain\s*:\s*\[", section_block, re.DOTALL)
        if not domain_match:
            return []

        list_start = section_block.find("[", domain_match.start())
        domain_block = SmailProWebClient._extract_balanced_block(section_block, list_start, "[", "]")
        if object_items:
            return re.findall(r'["\']name["\']\s*:\s*["\']([^"\']+)["\']', domain_block)
        return re.findall(r'["\']([^"\']+)["\']', domain_block)

    @staticmethod
    def _extract_servers(section_block: str) -> list[dict[str, Any]]:
        server_match = re.search(r"\bservers\s*:\s*\[", section_block, re.DOTALL)
        if not server_match:
            return []

        list_start = section_block.find("[", server_match.start())
        server_block = SmailProWebClient._extract_balanced_block(section_block, list_start, "[", "]")
        server_items = re.findall(r"\{[^{}]*\}", server_block, re.DOTALL)
        servers: list[dict[str, Any]] = []
        for item in server_items:
            name_match = re.search(r'["\']name["\']\s*:\s*["\']([^"\']+)["\']', item)
            accounts_match = re.search(r'["\']accounts["\']\s*:\s*(\d+)', item)
            premium_match = re.search(r'["\']premium["\']\s*:\s*(true|false)', item)
            servers.append(
                {
                    "name": name_match.group(1) if name_match else "",
                    "accounts": int(accounts_match.group(1)) if accounts_match else 0,
                    "premium": (premium_match.group(1) == "true") if premium_match else False,
                }
            )
        return servers

    @classmethod
    def parse_domain_catalog_from_html(cls, html: str) -> dict[str, Any]:
        settings_block = cls._extract_settings_block(html)
        google_block = cls._extract_named_section(settings_block, "google")
        microsoft_block = cls._extract_named_section(settings_block, "microsoft")
        other_block = cls._extract_named_section(settings_block, "other")

        google_domains = cls._extract_domain_names(google_block)
        microsoft_domains = cls._extract_domain_names(microsoft_block)
        other_domains = cls._extract_domain_names(other_block, object_items=True)

        catalog = {
            "google": {
                "domains": google_domains,
                "servers": cls._extract_servers(google_block),
            },
            "microsoft": {
                "domains": microsoft_domains,
                "servers": cls._extract_servers(microsoft_block),
            },
            "other": {
                "domains": other_domains,
                "servers": [],
            },
        }
        catalog["all"] = google_domains + microsoft_domains + other_domains
        return catalog

    @classmethod
    def fetch_domain_catalog(cls, timeout: int = 30) -> dict[str, Any]:
        response = requests.get(SMAILPRO_WEB_URL, timeout=timeout)
        response.raise_for_status()
        catalog = cls.parse_domain_catalog_from_html(response.text)
        catalog["page_status_code"] = response.status_code
        catalog["page_url"] = response.url
        return catalog

    def list_domains(self) -> list[str]:
        return self.fetch_domain_catalog().get("all", [])

    def health_check(self) -> dict[str, Any]:
        browser_ready = False
        browser_error = None
        domains: list[str] = []
        domain_groups: dict[str, list[str]] = {}
        server_groups: dict[str, list[dict[str, Any]]] = {}
        page_status_code = None
        page_url = self.temp_email_url

        try:
            catalog = self.fetch_domain_catalog()
            domains = catalog.get("all", [])
            domain_groups = {
                "google": catalog.get("google", {}).get("domains", []),
                "microsoft": catalog.get("microsoft", {}).get("domains", []),
                "other": catalog.get("other", {}).get("domains", []),
            }
            server_groups = {
                "google": catalog.get("google", {}).get("servers", []),
                "microsoft": catalog.get("microsoft", {}).get("servers", []),
                "other": catalog.get("other", {}).get("servers", []),
            }
            page_status_code = catalog.get("page_status_code")
            page_url = catalog.get("page_url") or page_url
        except Exception as exc:
            return {
                "available": False,
                "error": f"failed to fetch SmailPro page metadata: {exc}",
                "domains": [],
                "domain_groups": {},
                "server_groups": {},
                "page_status_code": page_status_code,
                "page_url": page_url,
                "browser_ready": False,
                "headless": self.headless,
                "auto_visible_fallback": SMAILPRO_WEB_AUTO_VISIBLE_FALLBACK,
            }

        try:
            self._ensure_page()
            browser_ready = True
        except Exception as exc:
            browser_error = str(exc)
        finally:
            try:
                self.close()
            except Exception:
                pass

        result: dict[str, Any] = {
            "available": bool(domains) and browser_ready,
            "error": browser_error,
            "domains": domains,
            "domain_groups": domain_groups,
            "server_groups": server_groups,
            "page_status_code": page_status_code,
            "page_url": page_url,
            "browser_ready": browser_ready,
            "headless": self.headless,
            "auto_visible_fallback": SMAILPRO_WEB_AUTO_VISIBLE_FALLBACK,
        }
        if self.headless:
            result["warning"] = (
                "headless mode may hit 'Captcha is invalid'; "
                + ("automatic visible-browser fallback is enabled" if SMAILPRO_WEB_AUTO_VISIBLE_FALLBACK else "automatic fallback is disabled")
            )
        return result

    @staticmethod
    def _is_captcha_invalid_error(payload: Any) -> bool:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        lowered = text.lower()
        return "captcha is invalid" in lowered or ('"code": 403' in lowered and "captcha" in lowered) or ('"code":403' in lowered and "captcha" in lowered)

    def _switch_to_visible_mode(self, reason: str):
        if not self.headless:
            return
        self._log(f"[SmailProWeb] Headless 模式触发风控，切换到可见浏览器重试: {reason}")
        try:
            self.close()
        except Exception:
            pass
        self.headless = False

    def _run_with_visible_fallback(self, action_name: str, func):
        try:
            return func()
        except Exception as exc:
            if self.headless and SMAILPRO_WEB_AUTO_VISIBLE_FALLBACK and self._is_captcha_invalid_error(str(exc)):
                self._switch_to_visible_mode(f"{action_name}: {exc}")
                return func()
            raise

    def _create_account_once(
        self,
        email_prefix: Optional[str] = None,
        domain: Optional[str] = None,
        password: Optional[str] = None,
        pattern: Optional[str] = None,
    ) -> DuckMailAccount:
        self._ensure_page()

        if pattern:
            query = self._parse_pattern(pattern)
        elif email_prefix and domain:
            query = self._parse_pattern(f"{email_prefix}@{domain}")
        elif domain:
            query = self._parse_pattern(f"random@{domain}")
        else:
            query = self._parse_pattern(SMAILPRO_WEB_PATTERN)

        result = self._execute_async(
            """
            const query = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[2];
            (async () => {
              try {
                if (!window.__smailproFetchWrapped) {
                  window.__smailproFetchLog = [];
                  const originalFetch = window.fetch.bind(window);
                  window.fetch = async (...args) => {
                    const input = args[0];
                    const init = args[1] || {};
                    const url = typeof input === 'string' ? input : input.url;
                    try {
                      const resp = await originalFetch(...args);
                      let body = '';
                      try { body = await resp.clone().text(); } catch (e) {}
                      window.__smailproFetchLog.push({url, method: init.method || 'GET', status: resp.status, body: body.slice(0, 500)});
                      return resp;
                    } catch (e) {
                      window.__smailproFetchLog.push({url, method: init.method || 'GET', error: String(e)});
                      throw e;
                    }
                  };
                  window.__smailproFetchWrapped = true;
                }

                const tempRoot = document.querySelector("div[x-data='TemporaryEmail()']");
                const tempApi = tempRoot && tempRoot._x_dataStack ? tempRoot._x_dataStack[0] : null;
                const createRoot = document.querySelector("div[x-data='create()']");
                const createApi = createRoot && createRoot._x_dataStack ? createRoot._x_dataStack[0] : null;
                if (!tempApi || !createApi) {
                  done({ok: false, error: 'SmailPro Alpine components not found'});
                  return;
                }

                createApi.query = query;
                createApi.emailType = ['gmail.com', 'googlemail.com'].includes(query.domain) ? 'google' : ([
                  'outlook.com','hotmail.com','outlook.kr','outlook.fr','outlook.com.vn','outlook.co.id','outlook.co.th','outlook.com.ar','outlook.co.il'
                ].includes(query.domain) ? 'microsoft' : 'other');
                createApi.action = 'create';
                createApi.generating = false;
                createApi.patternInput = `${query.username}@${query.domain}${query.server !== '1' ? '-' + query.server : ''}`;

                if (typeof tempApi.captcha !== 'function') {
                  done({ok: false, error: 'captcha provider not found'});
                  return;
                }

                const toQueryString = (obj) => {
                  const params = new URLSearchParams();
                  Object.entries(obj || {}).forEach(([key, value]) => {
                    if (value !== undefined && value !== null && value !== '') {
                      params.set(key, String(value));
                    }
                  });
                  const encoded = params.toString();
                  return encoded ? `?${encoded}` : '';
                };

                const captchaToken = await tempApi.captcha();
                const createUrl = `https://smailpro.com/app/create${toQueryString(query)}`;
                const createResp = await fetch(createUrl, {
                  method: 'GET',
                  headers: {
                    'Content-Type': 'application/json',
                    'x-captcha': captchaToken
                  }
                });
                const createText = await createResp.text();
                let createJson = null;
                try { createJson = JSON.parse(createText); } catch (e) {}
                if (!createResp.ok) {
                  done({ok: false, status: createResp.status, body: createText, fetchLog: window.__smailproFetchLog.slice(-10)});
                  return;
                }
                if (!createJson || !createJson.address) {
                  done({ok: false, error: 'SmailPro create response missing address', body: createText, fetchLog: window.__smailproFetchLog.slice(-10)});
                  return;
                }

                tempApi.selectedEmail = createJson;
                tempApi.emails = [createJson, ...(tempApi.emails || []).filter(item => item && item.address !== createJson.address)];

                const start = Date.now();
                while (Date.now() - start < timeoutMs) {
                  const selected = tempApi.selectedEmail;
                  const emails = tempApi.emails || [];
                  const candidate = selected || emails[0];
                  if (candidate && candidate.address) {
                    done({ok: true, selected: candidate, emails, fetchLog: window.__smailproFetchLog.slice(-10)});
                    return;
                  }
                  const lastCreate = (window.__smailproFetchLog || []).filter(item => (item.url || '').includes('/app/create')).slice(-1)[0];
                  if (lastCreate && lastCreate.status && lastCreate.status >= 400) {
                    done({ok: false, status: lastCreate.status, body: lastCreate.body, fetchLog: window.__smailproFetchLog.slice(-10)});
                    return;
                  }
                  await new Promise(resolve => setTimeout(resolve, 500));
                }
                done({ok: false, error: 'Timed out waiting for SmailPro email', fetchLog: window.__smailproFetchLog.slice(-10)});
              } catch (e) {
                done({ok: false, error: String(e), stack: e && e.stack || null});
              }
            })();
            """,
            query,
            SMAILPRO_WEB_TIMEOUT * 1000,
            timeout=SMAILPRO_WEB_TIMEOUT + 10,
        )

        if not result.get("ok"):
            raise RuntimeError(f"SmailPro web create failed: {json.dumps(result, ensure_ascii=False)[:1000]}")

        selected = result.get("selected") or {}
        address = selected.get("address")
        if not address:
            raise RuntimeError(f"SmailPro web did not return address: {json.dumps(result, ensure_ascii=False)[:1000]}")

        if not password:
            password = self.generate_password()

        self.account = DuckMailAccount(
            address=address,
            password=password,
            account_id=selected.get("key") or address,
            token="smailpro-web",
        )
        self.account_meta = selected
        self._log(f"[SmailProWeb] 创建邮箱成功: {address}")
        return self.account

    def create_account(
        self,
        email_prefix: Optional[str] = None,
        domain: Optional[str] = None,
        password: Optional[str] = None,
        pattern: Optional[str] = None,
    ) -> DuckMailAccount:
        return self._run_with_visible_fallback(
            "create_account",
            lambda: self._create_account_once(
                email_prefix=email_prefix,
                domain=domain,
                password=password,
                pattern=pattern,
            ),
        )

    def get_token(self, address: Optional[str] = None, password: Optional[str] = None) -> str:
        if not self.account:
            raise ValueError("未创建账户，请先调用 create_account()")
        return self.account.token or "smailpro-web"

    def list_messages(self) -> List[EmailMessage]:
        if not self.account or not self.account_meta:
            raise ValueError("未创建账户，请先调用 create_account()")

        self._ensure_page()
        provider = self._provider_for_email(self.account.address)
        result = self._execute_async(
            """
            const meta = arguments[0];
            const provider = arguments[1];
            const done = arguments[2];
            (async () => {
              try {
                const inboxUrls = {
                  other: 'https://api.sonjj.com/v1/temp_email/inbox',
                  google: 'https://api.sonjj.com/v1/temp_gmail/inbox',
                  microsoft: 'https://api.sonjj.com/v1/temp_outlook/inbox'
                };
                const payloadResp = await fetch('https://smailpro.com/app/inbox', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify([{address: meta.address, timestamp: meta.timestamp, key: meta.key}])
                });
                const payloadBody = await payloadResp.text();
                let payloadJson = [];
                try { payloadJson = JSON.parse(payloadBody); } catch (e) {}
                if (!payloadResp.ok) {
                  done({ok: false, status: payloadResp.status, body: payloadBody});
                  return;
                }
                const entry = payloadJson[0];
                if (!entry || !entry.payload) {
                  done({ok: true, messages: [], entry});
                  return;
                }
                const inboxResp = await fetch(`${inboxUrls[provider]}?payload=${encodeURIComponent(entry.payload)}`);
                const inboxText = await inboxResp.text();
                let inboxJson = {};
                try { inboxJson = JSON.parse(inboxText); } catch (e) {}
                if (!inboxResp.ok) {
                  done({ok: false, status: inboxResp.status, body: inboxText, entry});
                  return;
                }
                done({ok: true, messages: inboxJson.messages || [], entry});
              } catch (e) {
                done({ok: false, error: String(e), stack: e && e.stack || null});
              }
            })();
            """,
            self.account_meta,
            provider,
        )

        if not result.get("ok"):
            raise RuntimeError(f"SmailPro web inbox failed: {json.dumps(result, ensure_ascii=False)[:1000]}")

        entry = result.get("entry") or {}
        if entry.get("key"):
            self.account_meta["key"] = entry["key"]
        if entry.get("timestamp"):
            self.account_meta["timestamp"] = entry["timestamp"]

        messages = []
        for item in result.get("messages", []) or []:
            messages.append(
                EmailMessage(
                    id=str(item.get("mid") or ""),
                    subject=item.get("textSubject", ""),
                    from_address=item.get("textFrom", ""),
                    from_name=item.get("textFrom", ""),
                    created_at=item.get("textDate", ""),
                    seen=False,
                )
            )
        messages.sort(key=lambda x: x.created_at, reverse=True)
        self._log(f"[SmailProWeb] 获取到 {len(messages)} 封邮件")
        return messages

    def _get_message_once(self, message_id: str) -> EmailDetail:
        if not self.account or not self.account_meta:
            raise ValueError("未创建账户，请先调用 create_account()")

        self._ensure_page()
        provider = self._provider_for_email(self.account.address)
        result = self._execute_async(
            """
            const meta = arguments[0];
            const provider = arguments[1];
            const messageId = arguments[2];
            const timeoutMs = arguments[3];
            const done = arguments[4];
            (async () => {
              try {
                const messageUrls = {
                  other: 'https://api.sonjj.com/v1/temp_email/message',
                  google: 'https://api.sonjj.com/v1/temp_gmail/message',
                  microsoft: 'https://api.sonjj.com/v1/temp_outlook/message'
                };
                if (!window.grecaptcha) {
                  await new Promise((resolve, reject) => {
                    const s = document.createElement('script');
                    s.src = 'https://www.google.com/recaptcha/api.js?render=6Ldd8-IUAAAAAIdqbOociFKyeBGFsp3nNUM_6_SC';
                    s.async = true;
                    s.onload = resolve;
                    s.onerror = reject;
                    document.head.appendChild(s);
                  });
                }
                const tempRoot = document.querySelector("div[x-data='TemporaryEmail()']");
                const tempApi = tempRoot && tempRoot._x_dataStack ? tempRoot._x_dataStack[0] : null;
                if (!tempApi || typeof tempApi.captcha !== 'function') {
                  done({ok: false, error: 'captcha provider not found'});
                  return;
                }
                const token = await tempApi.captcha();
                const payloadResp = await fetch('https://smailpro.com/app/message?email=' + encodeURIComponent(meta.address) + '&mid=' + encodeURIComponent(messageId), {
                  method: 'GET',
                  headers: {'Content-Type': 'application/json', 'x-captcha': token}
                });
                const payloadText = await payloadResp.text();
                if (!payloadResp.ok) {
                  done({ok: false, status: payloadResp.status, body: payloadText});
                  return;
                }
                const msgResp = await fetch(`${messageUrls[provider]}?payload=${encodeURIComponent(payloadText)}`);
                const msgText = await msgResp.text();
                let msgJson = {};
                try { msgJson = JSON.parse(msgText); } catch (e) {}
                if (!msgResp.ok) {
                  done({ok: false, status: msgResp.status, body: msgText});
                  return;
                }
                done({ok: true, data: msgJson});
              } catch (e) {
                done({ok: false, error: String(e), stack: e && e.stack || null});
              }
            })();
            """,
            self.account_meta,
            provider,
            message_id,
            SMAILPRO_WEB_TIMEOUT * 1000,
            timeout=SMAILPRO_WEB_TIMEOUT + 10,
        )

        if not result.get("ok"):
            raise RuntimeError(f"SmailPro web message failed: {json.dumps(result, ensure_ascii=False)[:1000]}")

        data = result.get("data") or {}
        body = data.get("body", "") or ""
        self._log(f"[SmailProWeb] 获取邮件详情成功: message_id={message_id}, body_len={len(body)}")
        return EmailDetail(
            id=message_id,
            subject="",
            from_address="",
            text=body,
            html=[body] if body else [],
        )

    def get_message(self, message_id: str) -> EmailDetail:
        return self._run_with_visible_fallback(
            "get_message",
            lambda: self._get_message_once(message_id),
        )

    def is_verification_email(
        self,
        message: EmailMessage,
        subject_patterns: Optional[List[str]] = None,
        sender_whitelist: Optional[List[str]] = None,
    ) -> bool:
        subject_patterns = subject_patterns or DEFAULT_SUBJECT_PATTERNS
        sender_whitelist = sender_whitelist or VERIFICATION_SENDERS
        subject = (message.subject or "").lower()
        from_address = (message.from_address or "").lower()
        if sender_whitelist and any(sender.lower() in from_address for sender in sender_whitelist):
            return True
        return any(re.search(pattern, subject, re.IGNORECASE) for pattern in subject_patterns)

    def close(self):
        if not self.driver:
            return
        try:
            if self.window_handle and not self.owns_driver:
                self._switch_to_window()
                self.driver.close()
                if self.parent_window_handle:
                    self.driver.switch_to.window(self.parent_window_handle)
            elif self.owns_driver:
                self.driver.quit()
        except Exception:
            pass
        finally:
            if self.owns_driver:
                self.driver = None
            self.window_handle = None
            self.parent_window_handle = None
            if self.profile_dir and self.owns_driver and not SMAILPRO_WEB_SHARED_PROFILE:
                try:
                    shutil.rmtree(self.profile_dir, ignore_errors=True)
                except Exception:
                    pass
            if self.owns_driver:
                self.profile_dir = None
