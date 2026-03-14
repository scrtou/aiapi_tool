"""
自动注册模块 - 使用 Selenium 自动完成 chayns 用户注册流程

功能：
- 创建 DuckMail 临时邮箱
- 访问 chayns 登录页面
- 检测邮箱未注册后进入注册流程
- 填写注册表单
- 轮询 DuckMail 获取验证邮件
- 打开确认链接并设置密码
- 验证登录成功并返回凭证
"""

import time
import os
import re
import hashlib
import json
import base64
import threading
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from enum import Enum
from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

from pydantic import BaseModel, Field
from fastapi import HTTPException

import requests


# ============== 日志工具 ==============
def log_message(message):
    """打印带时间戳的日志消息"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [AutoRegister] {message}")


# ============== 全局锁 ==============
_autoregister_lock = threading.Lock()


# ============== 配置类 ==============
class AutoRegisterConfig:
    """自动注册配置"""
    
    # 站点配置
    TARGET_SITE_URL = "https://chayns.net/72975-29241"
    
    # DuckMail API 配置 - 使用官方 API
    DUCKMAIL_BASE_URL = os.getenv("DUCKMAIL_BASE_URL", "https://api.duckmail.sbs")
    DUCKMAIL_DOMAIN = os.getenv("DUCKMAIL_DOMAIN", "duckmail.sbs")
    
    # 超时配置
    GLOBAL_TIMEOUT_SECONDS = int(os.getenv("AUTOREGISTER_TIMEOUT", "180"))
    PAGE_WAIT_TIMEOUT = 20
    ELEMENT_WAIT_TIMEOUT = 15
    IMPLICIT_WAIT_SECONDS = 5
    DUCKMAIL_CREATE_MAX_ATTEMPTS = int(os.getenv("DUCKMAIL_CREATE_MAX_ATTEMPTS", "5"))
    EMAIL_POLL_INTERVAL = 3
    EMAIL_POLL_MAX_ATTEMPTS = 40  # 最多轮询40次 = 120秒
    
    # 密码配置
    DEFAULT_PASSWORD = os.getenv("AUTOREGISTER_DEFAULT_PASSWORD", "12345Abc")
    
    # 注册后调用的 API
    POST_REGISTER_API_URL = "https://cube.tobit.cloud/chayns-ai-chatbot/intercom/cascading"
    POST_REGISTER_API_BODY = {
        "message": "sidekick pro",
        "nerMode": "None",
        "siteId": "95247-09669"
    }
    
    # 获取用户设置的 API（检查 pro access）
    USER_SETTINGS_API_URL = "https://cube.tobit.cloud/ai-proxy/v1/userSettings/personId/{personId}"
    AUTH_API_BASE_URL = os.getenv("CHAYNS_AUTH_API_BASE_URL", "https://auth.tobit.com/v2")
    AUTH_CHECK_ALIAS_SITE_ID = os.getenv("CHAYNS_AUTH_CHECK_ALIAS_SITE_ID", "00000")
    AUTH_REGISTER_API_BASE_URL = os.getenv("CHAYNS_AUTH_REGISTER_API_BASE_URL", "https://cube.tobit.cloud/auth/v4")
    MCAPTCHA_BASE_URL = os.getenv("MCAPTCHA_BASE_URL", "https://captcha.tobit.cloud")
    
    # 元素匹配关键词
    LOGIN_BUTTON_TEXTS = ["Anmelden","anmelden", "login", "sign in", "einloggen", "starten"]
    CREATE_ACCOUNT_KEYWORDS = ["create account", "konto erstellen", "registrieren", "register","Register","sign up"]
    CONTINUE_BUTTON_TEXTS = ["weiter", "continue", "next", "fortfahren", "registrieren", "register"]
    SUBMIT_BUTTON_TEXTS = ["submit", "absenden", "senden", "bestätigen", "confirm", "registrieren", "register"]
    SET_PASSWORD_BUTTON_TEXTS = ["set password", "passwort festlegen", "passwort setzen", "password"]
    EMAIL_INPUT_KEYWORDS = ["email", "mail", "e-mail", "phone", "telefon"]
    SETUP_PAGE_KEYWORDS = ["setup", "site erstellen"]
    
    PASSWORD_KEYWORDS = ["password", "passwort", "kennwort"]
    
    # 姓名输入框关键词（支持德语和英语）
    FIRST_NAME_KEYWORDS = ["first", "vorname", "given", "forename", "froename"]
    LAST_NAME_KEYWORDS = ["last", "nachname", "family", "surname", "surame"]


# ============== 请求/响应模型 ==============
class AutoRegisterRequest(BaseModel):
    """自动注册请求"""
    first_name: str = Field(..., min_length=1, max_length=50, description="名")
    last_name: str = Field(..., min_length=1, max_length=50, description="姓")
    password: Optional[str] = Field(None, min_length=8, max_length=100, description="密码，留空使用默认密码")


class AutoRegisterResponse(BaseModel):
    """自动注册响应"""
    email: str = Field(..., description="注册的邮箱地址")
    password: str = Field(..., description="账户密码")
    userid: int = Field(..., description="用户 ID")
    personid: str = Field(..., description="Person ID")
    token: str = Field(..., description="登录 token")
    has_pro_access: Optional[bool] = Field(None, description="是否有 Pro 权限")


class AutoRegisterError(BaseModel):
    """错误响应"""
    error: str = Field(..., description="错误消息")
    code: int = Field(..., description="错误码")
    state: Optional[str] = Field(None, description="失败时的状态")


# ============== 状态枚举 ==============
class RegisterState(Enum):
    """注册流程状态"""
    INIT = "init"
    DUCKMAIL_CREATED = "duckmail_created"
    SITE_OPENED = "site_opened"
    LOGIN_ENTRY = "login_entry"
    EMAIL_ENTERED = "email_entered"
    BRANCH_DETECTED = "branch_detected"
    REGISTER_FORM = "register_form"
    WAITING_EMAIL = "waiting_email"
    CONFIRMATION_LINK = "confirmation_link"
    SET_PASSWORD = "set_password"
    VERIFY_LOGIN = "verify_login"
    COMPLETE = "complete"
    FAILED = "failed"


# ============== 自定义异常 ==============
class AutoRegisterException(HTTPException):
    """自动注册基础异常"""
    def __init__(self, message: str, code: int = 500, state: str = None):
        self.message = message
        self.code = code
        self.state = state
        super().__init__(status_code=code, detail={"error": message, "code": code, "state": state})


class EmailExistsException(AutoRegisterException):
    """邮箱已存在"""
    def __init__(self, email: str):
        super().__init__(f"邮箱已存在: {email}", 409, RegisterState.BRANCH_DETECTED.value)


class TimeoutExceededException(AutoRegisterException):
    """全局超时"""
    def __init__(self, message: str = "操作超时", state: str = None):
        super().__init__(message, 504, state)


class AssertionFailedException(AutoRegisterException):
    """流程断言失败"""
    def __init__(self, message: str, state: str = None):
        super().__init__(message, 422, state)


# ============== Chrome 工具函数 ==============
def get_chrome_options() -> Options:
    """获取 Chrome 选项"""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    return options


def get_chrome_driver() -> Service:
    """获取 Chrome Driver 服务"""
    # 优先使用本地安装的 chromedriver
    if os.path.exists('/usr/bin/chromedriver'):
        return Service('/usr/bin/chromedriver')
    elif os.path.exists('/usr/local/bin/chromedriver'):
        return Service('/usr/local/bin/chromedriver')
    else:
        return Service(ChromeDriverManager().install())


# ============== 自动注册类 ==============
class AutoRegister:
    """自动注册执行器"""
    
    def __init__(self, request: AutoRegisterRequest):
        self.first_name = request.first_name
        self.last_name = request.last_name
        self.password = request.password or AutoRegisterConfig.DEFAULT_PASSWORD
        
        self.state = RegisterState.INIT
        self.driver: Optional[webdriver.Chrome] = None
        self.email: Optional[str] = None
        self.duckmail_client = None
        
        self.start_time: Optional[float] = None
        self.step_times: dict = {}
        
        # 调试信息
        self.debug_screenshots: list = []
        self.debug_logs: list = []
    
    def _log(self, message: str):
        """记录日志"""
        log_message(message)
        self.debug_logs.append(f"{datetime.now().isoformat()} - {message}")
    
    def _step_start(self, step_name: str):
        """步骤开始"""
        self.step_times[step_name] = {"start": time.time()}
        self._log(f"步骤开始: {step_name}")
    
    def _step_end(self, step_name: str):
        """步骤结束"""
        if step_name in self.step_times:
            elapsed = time.time() - self.step_times[step_name]["start"]
            self.step_times[step_name]["elapsed"] = elapsed
            self._log(f"步骤完成: {step_name} (耗时 {elapsed:.2f}s)")
    
    def _check_timeout(self):
        """检查全局超时"""
        if self.start_time and (time.time() - self.start_time) > AutoRegisterConfig.GLOBAL_TIMEOUT_SECONDS:
            raise TimeoutExceededException(f"全局超时 ({AutoRegisterConfig.GLOBAL_TIMEOUT_SECONDS}s)", self.state.value)
    
    def _take_screenshot(self, name: str):
        """截取屏幕截图"""
        if self.driver:
            try:
                screenshot = self.driver.get_screenshot_as_base64()
                self.debug_screenshots.append({"name": name, "data": screenshot})
            except:
                pass
    
    def _dump_debug_info(self, label: str = ""):
        """输出调试信息"""
        if not self.driver:
            return
        
        try:
            self._log(f"{label} - 当前 URL: {self.driver.current_url}")
            self._log(f"{label} - 页面标题: {self.driver.title}")

            # 输出页面文本预览，便于判断当前处于哪个流程步骤
            page_text = self._get_page_text().strip()
            if page_text:
                page_preview = page_text[:200].replace("\n", " | ")
                self._log(f"{label} - 页面文本预览: {page_preview}")
            
            # 输出可见输入框
            inputs = self.driver.find_elements(By.CSS_SELECTOR, "input")
            visible_inputs = [i for i in inputs if i.is_displayed()]
            self._log(f"{label} - 可见输入框数量: {len(visible_inputs)}")
            for inp in visible_inputs[:5]:  # 最多输出5个
                inp_type = inp.get_attribute("type") or ""
                inp_name = inp.get_attribute("name") or ""
                inp_placeholder = inp.get_attribute("placeholder") or ""
                inp_value = (inp.get_attribute("value") or "")[:80]
                inp_disabled = inp.get_attribute("disabled") is not None
                inp_readonly = inp.get_attribute("readonly") is not None
                self._log(
                    f"  输入框: type={inp_type}, name={inp_name}, placeholder={inp_placeholder}, "
                    f"value={inp_value}, disabled={inp_disabled}, readonly={inp_readonly}"
                )
            
            # 输出可见按钮
            buttons = self.driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
            visible_buttons = [b for b in buttons if b.is_displayed()]
            self._log(f"{label} - 可见按钮数量: {len(visible_buttons)}")
            for btn in visible_buttons[:5]:
                btn_text = (btn.text or "").strip()[:50]
                self._log(f"  按钮: text={btn_text}")
            
            # 截图
            self._take_screenshot(label)
            
        except Exception as e:
            self._log(f"{label} - 输出调试信息失败: {e}")
    
    def _cleanup(self):
        """清理资源"""
        if self.duckmail_client and hasattr(self.duckmail_client, "close"):
            try:
                self.duckmail_client.close()
            except Exception:
                pass

        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None

    @contextmanager
    def _temporary_implicit_wait(self, seconds: int):
        """临时调整隐式等待，避免 find_elements 叠加卡顿"""
        if not self.driver:
            yield
            return

        try:
            self.driver.implicitly_wait(seconds)
            yield
        finally:
            try:
                self.driver.implicitly_wait(AutoRegisterConfig.IMPLICIT_WAIT_SECONDS)
            except Exception:
                pass

    def _get_page_text(self) -> str:
        """获取当前页面文本"""
        if not self.driver:
            return ""

        try:
            return self.driver.execute_script(
                r"""
                function norm(v) {
                    return (v || '').toString().replace(/\s+/g, ' ').trim();
                }
                function isVisible(el) {
                    try {
                        const style = window.getComputedStyle(el);
                        if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
                            return false;
                        }
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    } catch (e) {
                        return false;
                    }
                }
                function collect(root, out) {
                    if (!root || !root.querySelectorAll) {
                        return;
                    }
                    root.querySelectorAll('*').forEach(el => {
                        if (isVisible(el)) {
                            const text = norm(el.innerText || el.textContent);
                            if (text) {
                                out.push(text);
                            }
                        }
                        if (el.shadowRoot) {
                            collect(el.shadowRoot, out);
                        }
                    });
                }
                const texts = [];
                if (document.body) {
                    const bodyText = norm(document.body.innerText || document.body.textContent);
                    if (bodyText) {
                        texts.push(bodyText);
                    }
                }
                collect(document, texts);
                return Array.from(new Set(texts)).join('\n');
                """
            ) or ""
        except Exception:
            return ""

    def _find_elements_including_shadow(self, selector: str):
        """查找包含 shadow DOM 内部的元素"""
        if not self.driver:
            return []

        try:
            return self.driver.execute_script(
                """
                const selector = arguments[0];
                function collect(root, output) {
                    if (!root || !root.querySelectorAll) {
                        return;
                    }
                    root.querySelectorAll(selector).forEach(el => output.push(el));
                    root.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot) {
                            collect(el.shadowRoot, output);
                        }
                    });
                }
                const results = [];
                collect(document, results);
                return results;
                """,
                selector,
            ) or []
        except Exception:
            return []

    def _find_mcaptcha_iframe(self):
        """查找 mCaptcha iframe"""
        if not self.driver:
            return None

        with self._temporary_implicit_wait(0):
            for frame in self._find_elements_including_shadow("iframe[src*='captcha.tobit.cloud/widget']"):
                try:
                    src = (frame.get_attribute("src") or "").strip()
                    if src:
                        return frame
                except Exception:
                    continue
        return None

    def _get_mcaptcha_sitekey(self) -> Optional[str]:
        """从 mCaptcha iframe 中提取 sitekey"""
        frame = self._find_mcaptcha_iframe()
        if not frame:
            return None

        src = (frame.get_attribute("src") or "").strip()
        if not src:
            return None

        parsed = urlparse(src)
        values = parse_qs(parsed.query).get("sitekey") or []
        return values[0] if values else None

    def _generate_mcaptcha_token(self, sitekey: str) -> str:
        """通过 mCaptcha PoW 接口生成 token"""
        max_score = 340282366920938463463374607431768211455

        def prefixed(text: str) -> bytes:
            size = len(text)
            return bytes([size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF, (size >> 24) & 0xFF, 0, 0, 0, 0]) + text.encode()

        config_url = f"{AutoRegisterConfig.MCAPTCHA_BASE_URL}/api/v1/pow/config"
        verify_url = f"{AutoRegisterConfig.MCAPTCHA_BASE_URL}/api/v1/pow/verify"

        conf_resp = requests.post(config_url, json={"key": sitekey}, timeout=30)
        conf_resp.raise_for_status()
        conf = conf_resp.json()

        base = conf["salt"].encode() + prefixed(conf["string"])
        threshold = max_score - max_score // int(conf["difficulty_factor"])

        nonce = 0
        start = time.time()
        result = 0
        while result < threshold:
            nonce += 1
            digest = hashlib.sha256(base + str(nonce).encode()).digest()
            result = int.from_bytes(digest[:16], "big")

        elapsed_ms = int((time.time() - start) * 1000)
        payload = {
            "key": sitekey,
            "string": conf["string"],
            "nonce": nonce,
            "result": str(result),
            "time": elapsed_ms,
            "worker_type": "js",
        }

        self._log(
            f"mCaptcha PoW 完成: nonce={nonce}, difficulty={conf['difficulty_factor']}, elapsed_ms={elapsed_ms}"
        )

        verify_resp = requests.post(verify_url, json=payload, timeout=30)
        verify_resp.raise_for_status()
        data = verify_resp.json()
        token = data.get("token")
        if not token:
            raise AssertionFailedException("mCaptcha 未返回 token", self.state.value)
        return token

    def _inject_mcaptcha_token(self, token: str):
        """从 captcha iframe 向父页面 postMessage token"""
        frame = self._find_mcaptcha_iframe()
        if not frame:
            raise AssertionFailedException("未找到 mCaptcha iframe", self.state.value)

        self.driver.switch_to.frame(frame)
        try:
            self.driver.execute_script("window.parent.postMessage({token: arguments[0]}, '*');", token)
        finally:
            self.driver.switch_to.default_content()

    def _ensure_mcaptcha_token(self):
        """确保注册页面拿到 mCaptcha token"""
        self._switch_to_default_content()
        token = self.driver.execute_script("return window.mcaptchaToken || null")
        if token:
            self._log("mCaptcha token 已存在")
            return token

        sitekey = self._get_mcaptcha_sitekey()
        if not sitekey:
            raise AssertionFailedException("未找到 mCaptcha sitekey", self.state.value)

        self._log(f"开始生成 mCaptcha token: sitekey={sitekey}")
        token = self._generate_mcaptcha_token(sitekey)
        self.driver.execute_script("window.mcaptchaToken = arguments[0];", token)
        return token

    def _get_register_context(self) -> tuple[str, int]:
        """读取注册接口需要的 siteId / currentTapp"""
        context = self.driver.execute_script(
            """
            const siteId = window.chayns && chayns.env && chayns.env.site ? chayns.env.site.id : null;
            const currentTapp = window.chayns && chayns.env && chayns.env.parameters ? chayns.env.parameters.currentTapp : null;
            return {
                siteId,
                currentTapp: currentTapp || document.querySelector('[data-cw-tapp-id]')?.getAttribute('data-cw-tapp-id') || null
            };
            """
        )

        site_id = context.get("siteId")
        current_tapp = context.get("currentTapp")
        if not site_id or not current_tapp:
            raise AssertionFailedException("无法获取注册上下文信息", self.state.value)

        return str(site_id), int(current_tapp)

    def _submit_register_request(self, mcaptcha_token: str):
        """直接调用注册 API，绕过前端按钮状态"""
        site_id, current_tapp = self._get_register_context()
        payload = {
            "siteId": site_id,
            "identifier": self.email,
            "firstname": self.first_name,
            "lastname": self.last_name,
            "redirectTappId": current_tapp,
            "redirectSiteId": site_id,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "mcaptchatoken": mcaptcha_token,
        }

        self._log(f"直接调用注册 API: siteId={site_id}, currentTapp={current_tapp}, email={self.email}")
        response = requests.post(
            f"{AutoRegisterConfig.AUTH_REGISTER_API_BASE_URL}/register",
            json=payload,
            headers=headers,
            timeout=30,
        )

        preview = response.text[:300] if response.text else ""
        self._log(f"注册 API 响应: status={response.status_code}, body={preview}")

        if response.status_code == 201:
            return

        if response.status_code == 409:
            raise EmailExistsException(self.email)

        if response.status_code == 403:
            raise AssertionFailedException(f"注册被拒绝: {preview}", self.state.value)

        raise AutoRegisterException(f"注册失败: HTTP {response.status_code} - {preview}", 500, self.state.value)

    @staticmethod
    def _extract_code_from_confirmation_link(confirmation_link: str) -> Optional[str]:
        parsed = urlparse(confirmation_link)
        params = parse_qs(parsed.query)
        code = params.get("code") or []
        return code[0] if code else None

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        payload = token.split('.')[1]
        padding = '=' * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        return json.loads(decoded.decode('utf-8'))

    def _verify_registration_code(self, code: str) -> str:
        response = requests.post(
            f"{AutoRegisterConfig.AUTH_REGISTER_API_BASE_URL}/register/verify",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={
                "code": code,
                "password": self.password,
                "passwordRepeat": self.password,
            },
            timeout=30,
        )

        preview = response.text[:300] if response.text else ""
        self._log(f"register/verify 响应: status={response.status_code}, body={preview}")

        if response.status_code in (400, 409):
            raise AssertionFailedException("验证码无效或已过期", self.state.value)
        response.raise_for_status()

        token = response.json().get("token")
        if not token:
            raise AssertionFailedException("register/verify 未返回 token", self.state.value)
        return token

    def _apply_login_token_to_browser(self, token: str):
        self.driver.get(AutoRegisterConfig.TARGET_SITE_URL)
        WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )

        self.driver.execute_async_script(
            """
            const token = arguments[0];
            const done = arguments[1];
            try {
                if (!(window.chayns && typeof chayns.invokeCall === 'function')) {
                    done({ok:false, error:'invokeCall unavailable'});
                    return;
                }
                chayns.invokeCall({action:115,value:{tobitAccessToken:token,keepOverlay:true,teamLogin:false}});
                setTimeout(() => done({ok:true}), 4000);
            } catch (e) {
                done({ok:false, error:String(e)});
            }
            """,
            token,
        )

        WebDriverWait(self.driver, 30).until(
            lambda d: any(cookie['name'].startswith('at_') for cookie in d.get_cookies())
        )

    def _is_setup_page(self) -> bool:
        """判断是否进入了新的 chayns setup 顶层流程"""
        if not self.driver:
            return False

        current_url = (self.driver.current_url or "").lower()
        title = (self.driver.title or "").lower()
        page_text = self._get_page_text().lower()

        if "/setup" in current_url:
            return True

        return any(keyword in title or keyword in page_text for keyword in AutoRegisterConfig.SETUP_PAGE_KEYWORDS)

    def _switch_to_default_content(self):
        """切回主文档"""
        if not self.driver:
            return

        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

    def _switch_to_login_iframe_if_present(self, timeout: int = 2) -> bool:
        """如果登录 iframe 存在则切换进去"""
        if not self.driver:
            return False

        self._switch_to_default_content()

        with self._temporary_implicit_wait(0):
            try:
                WebDriverWait(self.driver, timeout).until(
                    EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[src*='login.chayns.net']"))
                )
                return True
            except TimeoutException:
                return False

    def _find_visible_password_input(self):
        """查找可见的密码输入框"""
        selector = (
            "input[type='password'], input[autocomplete='current-password'], "
            "input[name='password'], input[name*='pass'], input[id*='pass']"
        )

        with self._temporary_implicit_wait(0):
            for element in self._find_elements_including_shadow(selector):
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except Exception:
                    continue

        return None

    def _find_visible_email_input(self, allow_disabled: bool = False):
        """查找可见的邮箱输入框"""
        selector = "input[name='email-phone'], input[type='email'], input[autocomplete='email'], input[name*='mail']"

        with self._temporary_implicit_wait(0):
            for element in self._find_elements_including_shadow(selector):
                try:
                    if element.is_displayed() and (allow_disabled or element.is_enabled()):
                        return element
                except Exception:
                    continue

            for element in self._find_elements_including_shadow("input"):
                try:
                    if not element.is_displayed() or (not allow_disabled and not element.is_enabled()):
                        continue

                    inp_type = (element.get_attribute("type") or "").lower()
                    inp_name = (element.get_attribute("name") or "").lower()
                    inp_placeholder = (element.get_attribute("placeholder") or "").lower()
                    inp_autocomplete = (element.get_attribute("autocomplete") or "").lower()
                    combined = f"{inp_type} {inp_name} {inp_placeholder} {inp_autocomplete}"

                    if inp_type in ["hidden", "submit", "button", "password"]:
                        continue

                    if any(keyword in combined for keyword in AutoRegisterConfig.EMAIL_INPUT_KEYWORDS):
                        return element
                except Exception:
                    continue

        return None

    def _set_input_value_via_js(self, input_element, value: str):
        """通过 JS 直接写入 input 的值，并触发常见事件"""
        self.driver.execute_script(
            """
            const input = arguments[0];
            const value = arguments[1];
            if (!input) {
                return;
            }
            input.removeAttribute('readonly');
            input.removeAttribute('disabled');
            const prototype = window.HTMLInputElement && window.HTMLInputElement.prototype;
            const descriptor = prototype ? Object.getOwnPropertyDescriptor(prototype, 'value') : null;
            if (descriptor && descriptor.set) {
                descriptor.set.call(input, value);
            } else {
                input.value = value;
            }
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new Event('blur', { bubbles: true }));
            """,
            input_element,
            value,
        )

    def _advance_email_step_in_current_context(self, context_label: str, attempt: int) -> bool:
        """在当前上下文中推进邮箱输入步骤"""
        email_input = self._find_visible_email_input(allow_disabled=True)
        if not email_input:
            self._log(f"{context_label} 未定位到邮箱输入框")
            return False

        current_value = (email_input.get_attribute("value") or "").strip().lower()
        expected_value = (self.email or "").strip().lower()
        disabled = email_input.get_attribute("disabled") is not None
        readonly = email_input.get_attribute("readonly") is not None

        self._log(
            f"{context_label} 邮箱框状态: value='{current_value}', disabled={disabled}, readonly={readonly}"
        )

        if current_value != expected_value or disabled or readonly:
            self._enter_email_value(email_input, self.email, f"{context_label} ")
            time.sleep(0.5)
        else:
            self._log(f"{context_label} 邮箱已存在，继续推进")

        try:
            email_input.send_keys(Keys.ENTER)
            self._log(f"{context_label} 已发送 Enter")
        except Exception:
            pass

        try:
            self._log(f"{context_label} 仍停留在邮箱步骤，重试点击继续 (第 {attempt + 1} 次检测)")
            self._click_continue_button()
            time.sleep(2)
            return True
        except AssertionFailedException:
            self._log(f"{context_label} 未找到继续按钮")
            return False

    def _enter_email_value(self, email_input, email: str, log_prefix: str = ""):
        """向邮箱输入框写入邮箱地址"""
        disabled = email_input.get_attribute("disabled") is not None
        readonly = email_input.get_attribute("readonly") is not None

        if not disabled:
            try:
                email_input.clear()
                email_input.send_keys(email)
                email_input.send_keys(Keys.TAB)
            except Exception:
                self._set_input_value_via_js(email_input, email)
        else:
            self._set_input_value_via_js(email_input, email)

        current_value = (email_input.get_attribute("value") or "").strip()
        if current_value.lower() != email.lower() or readonly:
            self._set_input_value_via_js(email_input, email)
            current_value = (email_input.get_attribute("value") or "").strip()

        self._log(
            f"{log_prefix}已输入邮箱: {email} (current='{current_value}', disabled={disabled}, readonly={readonly})"
        )

    def _locate_name_inputs_once(self) -> tuple:
        """单次查找姓名输入框"""
        first_name_input = None
        last_name_input = None

        with self._temporary_implicit_wait(0):
            all_inputs = self._find_elements_including_shadow("input")

        visible_inputs = [i for i in all_inputs if i.is_displayed()]

        for inp in visible_inputs:
            try:
                inp_type = (inp.get_attribute("type") or "").lower()
                inp_name = (inp.get_attribute("name") or "").lower()
                inp_placeholder = (inp.get_attribute("placeholder") or "").lower()
                inp_autocomplete = (inp.get_attribute("autocomplete") or "").lower()
                inp_id = (inp.get_attribute("id") or "").lower()

                if inp_type in ["email", "tel", "phone", "password", "hidden", "submit", "button"]:
                    continue

                label_text = ""
                try:
                    label_text = (inp.get_attribute("aria-label") or "").lower()
                    if not label_text:
                        parent = inp.find_element(By.XPATH, "./..")
                        parent_text = (parent.text or "").lower()
                        if parent_text:
                            label_text = parent_text
                except Exception:
                    pass

                all_text = f"{inp_name} {inp_placeholder} {inp_autocomplete} {inp_id} {label_text}"

                if not first_name_input:
                    for kw in AutoRegisterConfig.FIRST_NAME_KEYWORDS:
                        if kw in all_text:
                            first_name_input = inp
                            break

                if not last_name_input:
                    for kw in AutoRegisterConfig.LAST_NAME_KEYWORDS:
                        if kw in all_text:
                            last_name_input = inp
                            break

                if first_name_input and last_name_input:
                    return (first_name_input, last_name_input)
            except Exception:
                continue

        text_inputs = []
        for inp in visible_inputs:
            try:
                inp_type = (inp.get_attribute("type") or "").lower()
                inp_name = (inp.get_attribute("name") or "").lower()
                inp_autocomplete = (inp.get_attribute("autocomplete") or "").lower()

                if inp_type in ["email", "tel", "phone", "password", "hidden", "submit", "button"]:
                    continue
                if "email" in inp_name or "phone" in inp_name:
                    continue
                if "email" in inp_autocomplete:
                    continue

                text_inputs.append(inp)
            except Exception:
                continue

        if len(text_inputs) >= 2:
            return (text_inputs[0], text_inputs[1])

        return (first_name_input, last_name_input)

    def _advance_setup_email_step(self, attempt: int):
        """在新的 /setup 流程中重新推进邮箱步骤"""
        self._switch_to_default_content()
        return self._advance_email_step_in_current_context("setup 页面", attempt)
    
    def _init_driver(self):
        """初始化 WebDriver"""
        if self.driver:
            return
        
        options = get_chrome_options()
        service = get_chrome_driver()
        self.driver = webdriver.Chrome(service=service, options=options)
        
        # 设置隐式等待
        self.driver.implicitly_wait(AutoRegisterConfig.IMPLICIT_WAIT_SECONDS)
    
    def _init_duckmail(self):
        """初始化 DuckMail 邮箱"""
        self._step_start("初始化 DuckMail")
        
        # 动态导入 DuckMailClient
        try:
            from libs.clients.duckmail_client import DuckMailClient
        except ImportError:
            from libs.clients.duckmail_client import DuckMailClient

        try:
            from libs.clients.mailcx_client import MailCxClient
        except ImportError:
            from libs.clients.mailcx_client import MailCxClient

        try:
            from libs.clients.smailpro_client import SmailProClient
        except ImportError:
            from libs.clients.smailpro_client import SmailProClient

        try:
            from libs.clients.smailpro_web_client import SmailProWebClient
        except ImportError:
            from libs.clients.smailpro_web_client import SmailProWebClient

        try:
            from libs.clients.moemail_client import MoeMailClient
        except ImportError:
            from libs.clients.moemail_client import MoeMailClient

        moemail_key = (os.getenv("MOEMAIL_API_KEY") or "").strip()

        if moemail_key:
            try:
                self.duckmail_client = MoeMailClient()
                account = self.duckmail_client.create_account()
                self.duckmail_client.get_token()

                status_code, payload = self._check_alias_status(account.address)
                if status_code not in (204, 409):
                    raise AutoRegisterException(
                        f"MoeMail 邮箱不可用: status={status_code}, payload={str(payload)[:200]}",
                        503,
                        self.state.value,
                    )

                self._log(f"优先使用 MoeMail 邮箱: {account.address}")

                self.email = account.address
                self.state = RegisterState.DUCKMAIL_CREATED
                self._step_end("初始化 DuckMail")
                self._log(f"创建邮箱成功: {self.email}")
                return
            except Exception as e:
                self._log(f"MoeMail 不可用，回退到其它邮箱服务: {e}")

        # 使用官方 API 地址
        self.duckmail_client = DuckMailClient(AutoRegisterConfig.DUCKMAIL_BASE_URL)

        account = None
        duckmail_error = None

        try:
            selected_domain = self._select_usable_duckmail_domain()
            self._log(f"选择 DuckMail 域名: {selected_domain}")

            last_create_error = None
            for attempt in range(AutoRegisterConfig.DUCKMAIL_CREATE_MAX_ATTEMPTS):
                try:
                    account = self.duckmail_client.create_account(domain=selected_domain)
                    break
                except Exception as e:
                    last_create_error = e
                    self._log(
                        f"DuckMail 创建邮箱失败，重试 {attempt + 1}/{AutoRegisterConfig.DUCKMAIL_CREATE_MAX_ATTEMPTS}: {e}"
                    )
                    time.sleep(1)
            else:
                raise last_create_error

            if not account or not account.address:
                raise AutoRegisterException("创建 DuckMail 邮箱失败", 500, self.state.value)

            self.duckmail_client.get_token()
        except Exception as e:
            duckmail_error = e
            self._log(f"DuckMail 不可用，切换到 MailCx: {e}")

        if duckmail_error:
            fallback_errors = []

            for client_name, client_factory in [
                ("MoeMail", lambda: MoeMailClient()),
                ("SmailPro", lambda: SmailProClient()),
                ("SmailProWeb", lambda: SmailProWebClient()),
                ("MailCx", lambda: MailCxClient()),
            ]:
                try:
                    self.duckmail_client = client_factory()
                    account = self.duckmail_client.create_account()
                    self.duckmail_client.get_token()

                    status_code, payload = self._check_alias_status(account.address)
                    if status_code not in (204, 409):
                        raise AutoRegisterException(
                            f"{client_name} 邮箱不可用: status={status_code}, payload={str(payload)[:200]}",
                            503,
                            self.state.value,
                        )

                    self._log(f"已切换到 {client_name} 邮箱: {account.address}")
                    break
                except Exception as e:
                    fallback_errors.append(f"{client_name}: {e}")
                    self._log(f"{client_name} 不可用: {e}")
            else:
                raise AutoRegisterException(
                    f"所有备用邮箱服务均不可用: {' | '.join(fallback_errors)}",
                    503,
                    self.state.value,
                )

        self.email = account.address
        self.state = RegisterState.DUCKMAIL_CREATED
        
        self._step_end("初始化 DuckMail")
        self._log(f"创建邮箱成功: {self.email}")

    def _check_alias_status(self, alias: str) -> tuple[int, dict]:
        """调用 chayns 注册别名检查接口"""
        url = f"{AutoRegisterConfig.AUTH_API_BASE_URL}/register/checkalias"
        params = {
            "alias": alias,
            "siteId": AutoRegisterConfig.AUTH_CHECK_ALIAS_SITE_ID,
        }

        response = requests.get(url, params=params, timeout=30)

        payload = {}
        if response.text:
            try:
                payload = response.json()
            except Exception:
                payload = {"raw": response.text[:300]}

        self._log(
            f"checkalias: alias={alias}, status={response.status_code}, payload={str(payload)[:200]}"
        )
        return response.status_code, payload

    def _select_usable_duckmail_domain(self) -> str:
        """选择未被 chayns 拉黑的 DuckMail 域名"""
        configured_domain = (AutoRegisterConfig.DUCKMAIL_DOMAIN or "").strip()

        try:
            domains = self.duckmail_client.list_domains()
            domain_names = [d.domain for d in domains if d.is_verified and d.domain]
        except Exception as e:
            self._log(f"获取 DuckMail 域名列表失败，回退到配置域名: {e}")
            return configured_domain

        candidates = []
        if configured_domain:
            candidates.append(configured_domain)
        candidates.extend(domain for domain in domain_names if domain not in candidates)

        if not candidates:
            return configured_domain

        for domain in candidates:
            probe_alias = f"{self.duckmail_client.generate_email_prefix()}@{domain}"

            try:
                status_code, payload = self._check_alias_status(probe_alias)
            except Exception as e:
                self._log(f"检测 DuckMail 域名可用性失败: domain={domain}, error={e}")
                continue

            if status_code in (204, 409):
                return domain

            if status_code == 403:
                error_code = (payload.get("errorCode") or "").lower()
                if "blacklisted_identifier" in error_code:
                    self._log(f"DuckMail 域名被 chayns 拉黑，跳过: {domain}")
                    continue

        raise AssertionFailedException("没有可用的 DuckMail 域名，当前域名均被目标站点拦截", self.state.value)
    
    def _open_site_and_login_entry(self):
        """打开站点并进入登录入口"""
        self._step_start("打开站点")
        
        self._init_driver()
        self.driver.get(AutoRegisterConfig.TARGET_SITE_URL)
        
        # 等待页面加载
        WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )
        
        self.state = RegisterState.SITE_OPENED
        self._log(f"站点已打开: {self.driver.current_url}")
        
        # 查找并点击登录按钮
        self._step_start("查找登录入口")
        
        try:
            # 先尝试通过 CSS 选择器找到登录按钮
            login_button = WebDriverWait(self.driver, AutoRegisterConfig.ELEMENT_WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.beta-chayns-button"))
            )
            self._log("找到登录按钮 (通过 beta-chayns-button 类)")
        except:
            # 兜底：通过文本查找
            try:
                found_button = None
                found_text = ""
                def find_login_button(d):
                    nonlocal found_button, found_text
                    for btn in d.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
                        if not btn.is_displayed():
                            continue
                        text = (btn.text or "").strip()
                        text_lower = text.lower()
                        if any(kw in text_lower for kw in AutoRegisterConfig.LOGIN_BUTTON_TEXTS):
                            found_button = btn
                            found_text = text
                            return btn
                    return None
                
                login_button = WebDriverWait(self.driver, AutoRegisterConfig.ELEMENT_WAIT_TIMEOUT).until(find_login_button)
                self._log(f"找到登录按钮 (通过文本匹配): '{found_text}'")
            except:
                self._dump_debug_info("未找到登录按钮")
                raise AssertionFailedException("未找到登录按钮", self.state.value)
        
        # 点击登录按钮
        self.driver.execute_script("arguments[0].click();", login_button)
        self._log("已点击登录按钮")
        
        self.state = RegisterState.LOGIN_ENTRY
        self._step_end("查找登录入口")
        self._step_end("打开站点")
    
    def _enter_email(self):
        """进入登录 iframe 并输入邮箱"""
        self._step_start("输入邮箱")
        
        # 等待并切换到登录 iframe
        try:
            WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
                EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[src*='login.chayns.net']"))
            )
            self._log("已切换到登录 iframe")
        except:
            self._dump_debug_info("未找到登录 iframe")
            raise AssertionFailedException("未找到登录 iframe", self.state.value)
        
        # 检查是否存在 "other user" 元素，如果有则点击
        try:
            other_user = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "/html/body/div[1]/div/div[1]/div/div[2]/div[2]/div/div/div[2]"))
            )
            other_user.click()
            self._log("点击了 'other user' 元素")
            time.sleep(1)
        except:
            self._log("未发现 'other user' 元素，继续")
        
        # 查找邮箱输入框
        try:
            email_input = WebDriverWait(self.driver, AutoRegisterConfig.ELEMENT_WAIT_TIMEOUT).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[name="email-phone"]'))
            )
        except:
            self._dump_debug_info("未找到邮箱输入框")
            raise AssertionFailedException("未找到邮箱输入框", self.state.value)
        
        # 输入邮箱
        self._enter_email_value(email_input, self.email)
        
        time.sleep(1)
        
        # 点击继续按钮
        self._click_continue_button()
        
        self.state = RegisterState.EMAIL_ENTERED
        self._step_end("输入邮箱")
    
    def _click_continue_button(self):
        """点击继续按钮"""
        # 1) 优先找 submit 按钮
        try:
            def find_submit(d):
                for b in self._find_elements_including_shadow("button[type='submit'], input[type='submit']"):
                    if b.is_displayed() and b.is_enabled():
                        return b
                return None
            
            btn = WebDriverWait(self.driver, 10).until(lambda d: find_submit(d) or True)
            if btn and btn is not True:
                self.driver.execute_script("arguments[0].click();", btn)
                self._log("点击了 submit 按钮")
                return
        except:
            pass
        
        # 2) 兜底：通过文本查找
        def find_text_button(d):
            for b in self._find_elements_including_shadow("button, [role='button'], input[type='button'], input[type='submit']"):
                if not b.is_displayed() or not b.is_enabled():
                    continue
                t = ((b.text or "").strip() or (b.get_attribute("value") or "").strip()).lower()
                if any(x in t for x in AutoRegisterConfig.CONTINUE_BUTTON_TEXTS):
                    return b
            return None
        
        try:
            btn = WebDriverWait(self.driver, 10).until(find_text_button)
            self.driver.execute_script("arguments[0].click();", btn)
            self._log("点击了继续按钮")
        except:
            self._dump_debug_info("未找到继续按钮")
            raise AssertionFailedException("未找到继续按钮", self.state.value)
    
    def _detect_branch(self) -> bool:
        """
        检测分支：是新用户（注册）还是已存在用户（登录）
        
        Returns:
            True: 新用户，进入注册流程
            False: 已存在用户
        """
        self._step_start("检测分支")
        
        time.sleep(2)  # 等待页面响应
        
        # 检查页面内容判断是注册还是登录
        # 方法1：检查是否出现密码输入框（已存在用户）
        # 方法2：检查是否出现 "Create account" 相关元素（新用户）
        
        max_attempts = 10
        for attempt in range(max_attempts):
            self._check_timeout()

            # 先检查顶层页面，兼容新的 /setup 流程
            self._switch_to_default_content()
            top_url = self.driver.current_url
            top_title = self.driver.title
            is_setup_page = self._is_setup_page()
            page_text = self._get_page_text().lower()

            self._log(
                f"分支检测上下文 {attempt + 1}/{max_attempts}: url={top_url}, title={top_title}, setup={is_setup_page}"
            )

            if is_setup_page:
                if self._find_visible_password_input():
                    self._log("setup 页面检测到密码输入框 - 邮箱已存在")
                    self._step_end("检测分支")
                    raise EmailExistsException(self.email)

                first_name_input, last_name_input = self._locate_name_inputs_once()
                if first_name_input and last_name_input:
                    self._log("检测到 setup 页面姓名输入框 - 新用户")
                    self.state = RegisterState.BRANCH_DETECTED
                    self._step_end("检测分支")
                    return True

                if self._advance_setup_email_step(attempt):
                    self._log("已处理 setup 页面邮箱步骤，等待下一步表单加载")
                    continue

                register_clicked = self._click_register_button()
                if register_clicked:
                    self._log("setup 页面已点击注册按钮，等待注册表单加载...")
                    time.sleep(2)

                    first_name_input, last_name_input = self._locate_name_inputs_once()
                    if first_name_input and last_name_input:
                        self._log("点击 setup 注册按钮后检测到姓名输入框 - 新用户")
                        self.state = RegisterState.BRANCH_DETECTED
                        self._step_end("检测分支")
                        return True

            # 再检查旧的登录 iframe/顶层登录页流程
            in_login_iframe = self._switch_to_login_iframe_if_present(timeout=3)
            if in_login_iframe:
                self._log("分支检测切换到登录 iframe")
            else:
                self._switch_to_default_content()

            if self._find_visible_password_input():
                self._log("检测到密码输入框 - 邮箱已存在")
                self._step_end("检测分支")
                raise EmailExistsException(self.email)

            if self._advance_email_step_in_current_context("登录 iframe", attempt):
                self._log("已处理登录 iframe 邮箱步骤，等待下一步表单加载")
                continue

            page_text = self._get_page_text().lower()

            for keyword in AutoRegisterConfig.CREATE_ACCOUNT_KEYWORDS:
                if keyword.lower() in page_text:
                    self._log(f"检测到注册关键词: {keyword} - 新用户")

                    register_clicked = self._click_register_button()
                    if register_clicked:
                        self._log("已点击注册按钮，等待注册表单加载...")
                        time.sleep(2)

                    self.state = RegisterState.BRANCH_DETECTED
                    self._step_end("检测分支")
                    return True

            first_name_input, last_name_input = self._locate_name_inputs_once()
            if first_name_input and last_name_input:
                self._log("检测到姓名输入框 - 新用户")
                self.state = RegisterState.BRANCH_DETECTED
                self._step_end("检测分支")
                return True
            
            time.sleep(1)
            self._log(f"分支检测尝试 {attempt + 1}/{max_attempts}...")
        
        # 超过最大尝试次数，输出调试信息
        self._dump_debug_info("分支检测失败")
        raise AssertionFailedException("无法确定注册/登录分支", self.state.value)
    
    def _click_register_button(self) -> bool:
        """
        查找并点击注册按钮（弹窗中的 Register/Registrieren 按钮）
        
        Returns:
            True: 成功点击
            False: 未找到按钮
        """
        # 注册按钮关键词
        register_keywords = ["register", "registrieren", "sign up"]
        
        # 多次尝试，避免 StaleElementReferenceException
        for attempt in range(3):
            try:
                # 每次循环重新获取按钮列表
                buttons = self._find_elements_including_shadow(
                    "button, [role='button'], a.button, a[class*='button'], input[type='button'], input[type='submit']"
                )
                
                for btn in buttons:
                    try:
                        if not btn.is_displayed():
                            continue
                        
                        text = ((btn.text or "").strip() or (btn.get_attribute("value") or "").strip()).lower()
                        
                        # 精确匹配 "Registrieren" 或 "Register"
                        if text in ["registrieren", "register"]:
                            self.driver.execute_script("arguments[0].click();", btn)
                            self._log(f"点击了注册按钮: '{text}'")
                            return True
                    except:
                        continue
                
                # 如果精确匹配失败，尝试模糊匹配
                buttons = self._find_elements_including_shadow(
                    "button, [role='button'], a.button, a[class*='button'], input[type='button'], input[type='submit']"
                )
                for btn in buttons:
                    try:
                        if not btn.is_displayed():
                            continue
                        
                        text = ((btn.text or "").strip() or (btn.get_attribute("value") or "").strip()).lower()
                        
                        for kw in register_keywords:
                            if kw in text and "zurück" not in text and "back" not in text:
                                self.driver.execute_script("arguments[0].click();", btn)
                                self._log(f"点击了注册按钮 (模糊匹配): '{text}'")
                                return True
                    except:
                        continue
                
                self._log(f"未找到注册按钮 (尝试 {attempt + 1}/3)")
                time.sleep(1)
                
            except Exception as e:
                self._log(f"点击注册按钮尝试 {attempt + 1} 失败: {e}")
                time.sleep(1)
        
        return False
    
    def _fill_register_form(self):
        """填写注册表单"""
        self._step_start("填写注册表单")
        self.state = RegisterState.REGISTER_FORM

        if self._is_setup_page():
            self._switch_to_default_content()
        
        # 注意：此时仍在 iframe 中，检测到 registrieren 关键词后，页面应该已经显示姓名输入框
        # 不要切回主框架，也不要点击任何按钮
        
        time.sleep(1)
        
        # 打印当前页面/iframe 内容用于调试
        try:
            page_text = self.driver.execute_script("return document.body ? document.body.innerText : ''")
            # 限制长度避免日志过长
            if page_text:
                page_text_preview = page_text[:500].replace('\n', ' | ')
                self._log(f"当前页面文本预览: {page_text_preview}")
                
                # 打印可见输入框的详细信息
                all_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input")
                visible_inputs = [i for i in all_inputs if i.is_displayed()]
                self._log(f"可见输入框数量: {len(visible_inputs)}")
                for idx, inp in enumerate(visible_inputs[:10]):
                    inp_type = inp.get_attribute("type") or ""
                    inp_name = inp.get_attribute("name") or ""
                    inp_placeholder = inp.get_attribute("placeholder") or ""
                    inp_autocomplete = inp.get_attribute("autocomplete") or ""
                    self._log(f"  输入框[{idx}]: type='{inp_type}', name='{inp_name}', placeholder='{inp_placeholder}', autocomplete='{inp_autocomplete}'")
        except Exception as e:
            self._log(f"打印页面内容失败: {e}")
        
        # 使用关键词匹配查找姓名输入框
        first_name_input, last_name_input = self._find_name_inputs()

        if (not first_name_input or not last_name_input) and self._is_setup_page():
            self._log("setup 页面尚未出现姓名输入框，重试推进邮箱步骤")
            if self._advance_setup_email_step(0):
                first_name_input, last_name_input = self._find_name_inputs()
        
        if not first_name_input or not last_name_input:
            self._dump_debug_info("未找到足够的姓名输入框")
            raise AssertionFailedException("未找到姓名输入框", self.state.value)
        
        # 填写姓名
        first_name_input.clear()
        first_name_input.send_keys(self.first_name)
        self._log(f"输入名字: {self.first_name}")
        
        time.sleep(0.5)
        
        last_name_input.clear()
        last_name_input.send_keys(self.last_name)
        self._log(f"输入姓氏: {self.last_name}")

        time.sleep(1)

        if self._is_setup_page():
            token = self._ensure_mcaptcha_token()
            self._submit_register_request(token)
        else:
            self._click_continue_button()

        self._step_end("填写注册表单")
    
    def _find_name_inputs(self) -> tuple:
        """
        通过关键词匹配查找姓名输入框（类似密码输入框的查找方式）
        
        Returns:
            (first_name_input, last_name_input) 元组，找不到时对应位置为 None
        """
        first_name_input = None
        last_name_input = None

        for attempt in range(10):
            self._check_timeout()

            first_name_input, last_name_input = self._locate_name_inputs_once()

            if first_name_input and last_name_input:
                self._log("找到姓名输入框")
                return (first_name_input, last_name_input)

            self._log(f"查找姓名输入框尝试 {attempt + 1}/10...")
            time.sleep(2)
        
        return (first_name_input, last_name_input)
    
    def _wait_for_confirmation_link(self) -> str:
        """
        轮询 DuckMail 等待验证邮件
        
        Returns:
            确认链接 URL
        """
        self._step_start("等待验证邮件")
        self.state = RegisterState.WAITING_EMAIL
        
        # 导入链接提取器
        try:
            from libs.clients.duckmail_client import LinkExtractor
        except ImportError:
            from libs.clients.duckmail_client import LinkExtractor
        
        confirmation_link = None
        seen_ids = set()
        
        for attempt in range(AutoRegisterConfig.EMAIL_POLL_MAX_ATTEMPTS):
            self._check_timeout()
            
            self._log(f"轮询邮件尝试 {attempt + 1}/{AutoRegisterConfig.EMAIL_POLL_MAX_ATTEMPTS}...")
            
            try:
                # 使用 DuckMailClient 的 list_messages 方法
                messages = self.duckmail_client.list_messages()
                
                if messages and len(messages) > 0:
                    self._log(f"收到 {len(messages)} 封邮件")
                    
                    for msg in messages:
                        # 跳过已检查过的
                        if msg.id in seen_ids:
                            continue
                        seen_ids.add(msg.id)
                        
                        # 检查是否为验证邮件
                        if self.duckmail_client.is_verification_email(msg):
                            self._log(f"找到验证邮件: id={msg.id}, subject='{msg.subject}'")
                            
                            # 获取邮件详情
                            detail = self.duckmail_client.get_message(msg.id)
                            
                            # 提取确认链接
                            confirmation_link = LinkExtractor.extract_confirmation_link(detail)
                            
                            if confirmation_link:
                                self._log(f"找到确认链接: {confirmation_link[:80]}...")
                                break
                    
                    if confirmation_link:
                        break
            
            except Exception as e:
                self._log(f"获取邮件失败: {e}")
            
            time.sleep(AutoRegisterConfig.EMAIL_POLL_INTERVAL)
        
        if not confirmation_link:
            raise TimeoutExceededException("等待验证邮件超时", self.state.value)
        
        self.state = RegisterState.CONFIRMATION_LINK
        self._step_end("等待验证邮件")
        
        return confirmation_link
    
    def _open_confirmation_link_and_set_password(self, confirmation_link: str):
        """打开确认链接并设置密码"""
        self._step_start("设置密码")
        self.state = RegisterState.SET_PASSWORD
        code = self._extract_code_from_confirmation_link(confirmation_link)
        if not code:
            raise AssertionFailedException("确认链接中缺少 code 参数", self.state.value)

        verify_token = self._verify_registration_code(code)
        self._apply_login_token_to_browser(verify_token)
        time.sleep(2)
        
        self._step_end("设置密码")
    
    def _find_password_inputs(self) -> list:
        """查找密码输入框"""
        # 多种选择器
        selectors = [
            "input[type='password']",
            "input[autocomplete='new-password']",
            "input[name*='password']",
            "input[name*='pass']",
            "input[placeholder*='password']",
            "input[placeholder*='Password']",
            "input[placeholder*='Passwort']",
        ]
        
        password_inputs = []
        seen_elements = set()
        
        for sel in selectors:
            try:
                inputs = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for inp in inputs:
                    if inp.is_displayed() and id(inp) not in seen_elements:
                        password_inputs.append(inp)
                        seen_elements.add(id(inp))
            except:
                continue
        
        # 如果还是没找到，尝试更宽泛的搜索
        if not password_inputs:
            all_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input")
            for inp in all_inputs:
                if not inp.is_displayed():
                    continue
                
                inp_type = (inp.get_attribute("type") or "").lower()
                placeholder = (inp.get_attribute("placeholder") or "").lower()
                name = (inp.get_attribute("name") or "").lower()
                
                is_password_field = (
                    inp_type == "password" or
                    any(kw in placeholder for kw in AutoRegisterConfig.PASSWORD_KEYWORDS) or
                    any(kw in name for kw in AutoRegisterConfig.PASSWORD_KEYWORDS)
                )
                
                if is_password_field:
                    password_inputs.append(inp)
        
        # 去重并按页面位置排序
        unique_inputs = []
        seen_locations = set()
        
        for inp in password_inputs:
            try:
                loc = inp.location
                loc_key = (loc['x'], loc['y'])
                if loc_key not in seen_locations:
                    unique_inputs.append(inp)
                    seen_locations.add(loc_key)
            except:
                unique_inputs.append(inp)
        
        # 按 y 坐标排序（从上到下）
        try:
            unique_inputs.sort(key=lambda x: x.location['y'])
        except:
            pass
        
        return unique_inputs
    
    def _find_set_password_button(self) -> Optional[any]:
        """查找设置密码按钮"""
        buttons = self.driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
        
        # 优先精确匹配 "Set password"
        for btn in buttons:
            if not btn.is_displayed():
                continue
            text = (btn.text or "").strip()
            text_lower = text.lower()
            
            # 精确匹配 "Set password"
            if text_lower == "set password" or text_lower == "passwort festlegen":
                return btn
        
        # 其次模糊匹配
        for btn in buttons:
            if not btn.is_displayed():
                continue
            text = (btn.text or "").strip()
            text_lower = text.lower()
            
            for keyword in AutoRegisterConfig.SET_PASSWORD_BUTTON_TEXTS:
                if keyword.lower() in text_lower:
                    return btn
        
        return None
    
    def _verify_login_and_extract_credentials(self) -> dict:
        """验证登录状态并提取凭证"""
        self._step_start("验证登录")
        self.state = RegisterState.VERIFY_LOGIN
        
        # 切回主框架
        try:
            self.driver.switch_to.default_content()
        except:
            pass
        
        # 等待页面加载
        WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )
        
        self._log(f"页面标题: {self.driver.title}")
        self._log(f"当前 URL: {self.driver.current_url}")
        
        # 等待 at_ cookie 出现
        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: any(cookie['name'].startswith('at_') for cookie in d.get_cookies())
            )
            self._log("检测到 at_ cookie")
        except Exception as e:
            self._dump_debug_info("等待 at_ cookie 超时")
            raise AssertionFailedException(f"等待登录态 cookie 超时: {e}", self.state.value)
        
        # 获取 at_ cookie
        at_cookie = None
        for cookie in self.driver.get_cookies():
            if cookie['name'].startswith('at_'):
                at_cookie = cookie
                break
        
        if not at_cookie:
            raise AssertionFailedException("未找到 at_ cookie", self.state.value)
        
        token = at_cookie["value"]
        self._log(f"获取到 token: {token[:20]}...")

        try:
            token_payload = self._decode_jwt_payload(token)
            userid = token_payload.get("TobitUserID") or token_payload.get("userId") or token_payload.get("userid")
            personid = token_payload.get("PersonID") or token_payload.get("personId")
            if userid and personid:
                result = {
                    "email": self.email,
                    "password": self.password,
                    "userid": int(userid),
                    "personid": str(personid),
                    "token": token,
                }
                self._step_end("验证登录")
                self._log(f"登录验证成功(JWT): userid={result['userid']}, personid={result['personid']}")
                return result
        except Exception as e:
            self._log(f"解析登录 JWT 失败，回退到页面信息: {e}")

        # 等待 window.cwInfo 对象
        try:
            WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
                lambda d: d.execute_script("return typeof window.cwInfo !== 'undefined' && window.cwInfo.user;")
            )
        except Exception as e:
            self._log(f"等待 window.cwInfo 超时: {e}")
            # 尝试刷新页面
            self.driver.refresh()
            time.sleep(3)
            try:
                WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
                    lambda d: d.execute_script("return typeof window.cwInfo !== 'undefined' && window.cwInfo.user;")
                )
            except:
                raise AssertionFailedException("等待用户信息超时", self.state.value)
        
        # 获取用户信息
        user_info = self.driver.execute_script("return window.cwInfo;")
        
        if not user_info or "user" not in user_info:
            raise AssertionFailedException("用户信息不完整", self.state.value)
        
        user = user_info["user"]
        if "personId" not in user or "id" not in user:
            raise AssertionFailedException(f"用户信息字段缺失: {user}", self.state.value)
        
        result = {
            "email": self.email,
            "password": self.password,
            "userid": int(user["id"]),
            "personid": str(user["personId"]),
            "token": token,
        }
        
        self._step_end("验证登录")
        self._log(f"登录验证成功: userid={result['userid']}, personid={result['personid']}")
        
        return result
    
    def _call_post_register_api(self, token: str):
        """注册成功后调用 API"""
        self._step_start("调用注册后 API")
        
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            
            response = requests.post(
                AutoRegisterConfig.POST_REGISTER_API_URL,
                json=AutoRegisterConfig.POST_REGISTER_API_BODY,
                headers=headers,
                timeout=30
            )
            
            self._log(f"注册后 API 调用完成: status_code={response.status_code}")
            
            if response.status_code >= 400:
                self._log(f"注册后 API 返回错误: {response.text[:200]}")
            else:
                self._log(f"注册后 API 返回成功: {response.text[:200]}")
                
        except Exception as e:
            self._log(f"注册后 API 调用失败: {e}")
            # 不抛出异常，注册已完成，API 调用失败不影响结果
        
        self._step_end("调用注册后 API")
    
    def _get_user_pro_access(self, token: str, person_id: str) -> Optional[bool]:
        """
        获取用户 Pro 权限状态
        
        Args:
            token: 用户登录 token
            person_id: 用户 Person ID
            
        Returns:
            True: 有 Pro 权限
            False: 没有 Pro 权限
            None: 获取失败
        """
        self._step_start("获取 Pro 权限状态")
        
        # 延迟 2 秒执行，确保后端数据已同步
        time.sleep(3)
        
        try:
            url = AutoRegisterConfig.USER_SETTINGS_API_URL.format(personId=person_id)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            self._log(f"获取用户设置 API 调用完成: status_code={response.status_code}")
            if response.status_code == 200:
                data = response.json()
                has_pro_access = data.get("hasProAccess", None)
                self._log(f"用户 Pro 权限状态: {has_pro_access}")
                self._step_end("获取 Pro 权限状态")
                return has_pro_access
            else:
                self._log(f"获取用户设置 API 返回错误: {response.status_code} - {response.text[:200]}")
                
        except Exception as e:
            self._log(f"获取用户设置 API 调用失败: {e}")
        
        self._step_end("获取 Pro 权限状态")
        return None
    
    def execute(self) -> dict:
        """执行自动注册流程"""
        self.start_time = time.time()
        self._log("开始自动注册流程")
        
        try:
            # 1. 初始化 DuckMail
            self._init_duckmail()
            self._check_timeout()
            
            # 2. 打开站点并进入登录入口
            self._open_site_and_login_entry()
            self._check_timeout()
            
            # 3. 输入邮箱
            self._enter_email()
            self._check_timeout()
            
            # 4. 检测分支
            is_new_user = self._detect_branch()
            self._check_timeout()
            
            if not is_new_user:
                # 理论上 _detect_branch 会抛出 EmailExistsException
                raise EmailExistsException(self.email)
            
            # 5. 填写注册表单
            self._fill_register_form()
            self._check_timeout()
            
            # 6. 等待验证邮件
            confirmation_link = self._wait_for_confirmation_link()
            self._check_timeout()
            
            # 7. 打开确认链接并设置密码
            self._open_confirmation_link_and_set_password(confirmation_link)
            # 密码设置成功后，不再检查超时，确保流程能够完成
            
            # 8. 验证登录并提取凭证
            result = self._verify_login_and_extract_credentials()
            
            # 9. 调用注册后 API
            self._call_post_register_api(result["token"])
            time.sleep(1)
            
            # 10. 获取用户 Pro 权限状态
            has_pro_access = self._get_user_pro_access(result["token"], result["personid"])
            result["has_pro_access"] = has_pro_access
            
            self.state = RegisterState.COMPLETE
            total_time = time.time() - self.start_time
            self._log(f"自动注册流程完成，总耗时 {total_time:.1f}s")
            
            return result
            
        except AutoRegisterException:
            self.state = RegisterState.FAILED
            raise
        except Exception as e:
            self.state = RegisterState.FAILED
            self._dump_debug_info("未预期异常")
            raise AutoRegisterException(str(e), 500, self.state.value)


# ============== API 处理函数 ==============
def handle_autoregister(request: AutoRegisterRequest) -> AutoRegisterResponse:
    """
    处理自动注册请求
    
    使用全局锁保证串行执行
    """
    # 尝试获取锁
    acquired = _autoregister_lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="服务繁忙，已有自动注册任务正在执行，请稍后重试"
        )
    
    try:
        log_message(f"收到自动注册请求: first_name={request.first_name}, last_name={request.last_name}")
        
        auto_register = AutoRegister(request)
        result = auto_register.execute()
        
        return AutoRegisterResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        log_message(f"自动注册失败: {e}")
        if isinstance(e, AutoRegisterException):
            raise
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _autoregister_lock.release()
        # 清理资源
        if 'auto_register' in locals():
            auto_register._cleanup()


# ============== 独立函数：获取用户 Pro 权限 ==============
def get_user_pro_access(token: str, person_id: str) -> Optional[bool]:
    """
    获取用户 Pro 权限状态（独立函数，可供其他模块调用）
    
    Args:
        token: 用户登录 token
        person_id: 用户 Person ID
        
    Returns:
        True: 有 Pro 权限
        False: 没有 Pro 权限
        None: 获取失败
    """
    try:
        url = AutoRegisterConfig.USER_SETTINGS_API_URL.format(personId=person_id)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        log_message(f"获取用户设置 API 调用完成: status_code={response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            has_pro_access = data.get("hasProAccess", None)
            log_message(f"用户 Pro 权限状态: {has_pro_access}")
            return has_pro_access
        else:
            log_message(f"获取用户设置 API 返回错误: {response.status_code} - {response.text[:200]}")
            
    except Exception as e:
        log_message(f"获取用户设置 API 调用失败: {e}")
    
    return None
