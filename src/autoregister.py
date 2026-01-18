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
import threading
from typing import Optional
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
    
    # 元素匹配关键词
    LOGIN_BUTTON_TEXTS = ["Anmelden","anmelden", "login", "sign in", "einloggen", "starten"]
    CREATE_ACCOUNT_KEYWORDS = ["create account", "konto erstellen", "registrieren", "register", "sign up"]
    CONTINUE_BUTTON_TEXTS = ["weiter", "continue", "next", "fortfahren", "registrieren", "register"]
    SUBMIT_BUTTON_TEXTS = ["submit", "absenden", "senden", "bestätigen", "confirm", "registrieren", "register"]
    SET_PASSWORD_BUTTON_TEXTS = ["set password", "passwort festlegen", "passwort setzen", "password"]
    
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
            
            # 输出可见输入框
            inputs = self.driver.find_elements(By.CSS_SELECTOR, "input")
            visible_inputs = [i for i in inputs if i.is_displayed()]
            self._log(f"{label} - 可见输入框数量: {len(visible_inputs)}")
            for inp in visible_inputs[:5]:  # 最多输出5个
                inp_type = inp.get_attribute("type") or ""
                inp_name = inp.get_attribute("name") or ""
                inp_placeholder = inp.get_attribute("placeholder") or ""
                self._log(f"  输入框: type={inp_type}, name={inp_name}, placeholder={inp_placeholder}")
            
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
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
    
    def _init_driver(self):
        """初始化 WebDriver"""
        if self.driver:
            return
        
        options = get_chrome_options()
        service = get_chrome_driver()
        self.driver = webdriver.Chrome(service=service, options=options)
        
        # 设置隐式等待
        self.driver.implicitly_wait(5)
    
    def _init_duckmail(self):
        """初始化 DuckMail 邮箱"""
        self._step_start("初始化 DuckMail")
        
        # 动态导入 DuckMailClient
        try:
            from src.duckmail_client import DuckMailClient
        except ImportError:
            from duckmail_client import DuckMailClient
        
        # 使用官方 API 地址
        self.duckmail_client = DuckMailClient(AutoRegisterConfig.DUCKMAIL_BASE_URL)
        
        # 创建账户
        account = self.duckmail_client.create_account(domain=AutoRegisterConfig.DUCKMAIL_DOMAIN)
        
        if not account or not account.address:
            raise AutoRegisterException("创建 DuckMail 邮箱失败", 500, self.state.value)
        
        # 获取 token
        self.duckmail_client.get_token()
        
        self.email = account.address
        self.state = RegisterState.DUCKMAIL_CREATED
        
        self._step_end("初始化 DuckMail")
        self._log(f"创建邮箱成功: {self.email}")
    
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
        email_input.clear()
        email_input.send_keys(self.email)
        email_input.send_keys(Keys.TAB)
        self._log(f"已输入邮箱: {self.email}")
        
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
                for b in d.find_elements(By.CSS_SELECTOR, "button[type='submit']"):
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
            for b in d.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
                if not b.is_displayed() or not b.is_enabled():
                    continue
                t = (b.text or "").strip().lower()
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
            
            # 检查是否有密码输入框（已存在用户的标志）
            password_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            visible_password = [p for p in password_inputs if p.is_displayed()]
            
            if visible_password:
                self._log("检测到密码输入框 - 邮箱已存在")
                self._step_end("检测分支")
                raise EmailExistsException(self.email)
            
            # 检查是否有注册相关元素（弹窗）
            page_text = self.driver.execute_script("return document.body && document.body.innerText ? document.body.innerText.toLowerCase() : ''")
            
            for keyword in AutoRegisterConfig.CREATE_ACCOUNT_KEYWORDS:
                if keyword.lower() in page_text:
                    self._log(f"检测到注册关键词: {keyword} - 新用户")
                    
                    # 查找并点击注册按钮（弹窗中的 Register/Registrieren 按钮）
                    register_clicked = self._click_register_button()
                    if register_clicked:
                        self._log("已点击注册按钮，等待注册表单加载...")
                        time.sleep(2)  # 等待注册表单加载
                    
                    self.state = RegisterState.BRANCH_DETECTED
                    self._step_end("检测分支")
                    return True
            
            # 检查是否有 first name / last name 输入框（注册表单的标志）
            name_inputs = self.driver.find_elements(By.CSS_SELECTOR,
                "input[name*='name'], input[name*='first'], input[name*='last'], input[placeholder*='name'], input[placeholder*='Name']")
            visible_name_inputs = [n for n in name_inputs if n.is_displayed()]
            
            if visible_name_inputs:
                self._log(f"检测到姓名输入框 ({len(visible_name_inputs)}个) - 新用户")
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
                buttons = self.driver.find_elements(By.CSS_SELECTOR, "button, [role='button'], a.button, a[class*='button']")
                
                for btn in buttons:
                    try:
                        if not btn.is_displayed():
                            continue
                        
                        text = (btn.text or "").strip().lower()
                        
                        # 精确匹配 "Registrieren" 或 "Register"
                        if text in ["registrieren", "register"]:
                            self.driver.execute_script("arguments[0].click();", btn)
                            self._log(f"点击了注册按钮: '{text}'")
                            return True
                    except:
                        continue
                
                # 如果精确匹配失败，尝试模糊匹配
                buttons = self.driver.find_elements(By.CSS_SELECTOR, "button, [role='button'], a.button, a[class*='button']")
                for btn in buttons:
                    try:
                        if not btn.is_displayed():
                            continue
                        
                        text = (btn.text or "").strip().lower()
                        
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
        
        # 点击继续/提交按钮
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
            
            # 获取所有可见输入框
            all_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input")
            visible_inputs = [i for i in all_inputs if i.is_displayed()]
            
            # 通过关键词匹配查找
            for inp in visible_inputs:
                try:
                    inp_type = (inp.get_attribute("type") or "").lower()
                    inp_name = (inp.get_attribute("name") or "").lower()
                    inp_placeholder = (inp.get_attribute("placeholder") or "").lower()
                    inp_autocomplete = (inp.get_attribute("autocomplete") or "").lower()
                    inp_id = (inp.get_attribute("id") or "").lower()
                    
                    # 跳过非文本输入框
                    if inp_type in ["email", "tel", "phone", "password", "hidden", "submit", "button"]:
                        continue
                    
                    # 获取相邻的 label 文本（通过父元素或前一个兄弟元素）
                    label_text = ""
                    try:
                        # 尝试通过 aria-label 获取
                        label_text = (inp.get_attribute("aria-label") or "").lower()
                        
                        # 尝试通过父元素获取文本
                        if not label_text:
                            parent = inp.find_element(By.XPATH, "./..")
                            parent_text = (parent.text or "").lower()
                            if parent_text:
                                label_text = parent_text
                    except:
                        pass
                    
                    # 合并所有可匹配的文本
                    all_text = f"{inp_name} {inp_placeholder} {inp_autocomplete} {inp_id} {label_text}"
                    
                    # 检查是否为名字输入框
                    if not first_name_input:
                        for kw in AutoRegisterConfig.FIRST_NAME_KEYWORDS:
                            if kw in all_text:
                                first_name_input = inp
                                self._log(f"找到名字输入框 (关键词: {kw})")
                                break
                    
                    # 检查是否为姓氏输入框
                    if not last_name_input:
                        for kw in AutoRegisterConfig.LAST_NAME_KEYWORDS:
                            if kw in all_text:
                                last_name_input = inp
                                self._log(f"找到姓氏输入框 (关键词: {kw})")
                                break
                    
                    if first_name_input and last_name_input:
                        break
                        
                except Exception as e:
                    continue
            
            if first_name_input and last_name_input:
                return (first_name_input, last_name_input)
            
            # 如果关键词匹配失败，尝试通用方法：取前两个文本输入框
            if not first_name_input or not last_name_input:
                text_inputs = []
                for inp in visible_inputs:
                    try:
                        inp_type = (inp.get_attribute("type") or "").lower()
                        inp_name = (inp.get_attribute("name") or "").lower()
                        inp_autocomplete = (inp.get_attribute("autocomplete") or "").lower()
                        
                        # 只要文本类型输入框
                        if inp_type in ["email", "tel", "phone", "password", "hidden", "submit", "button"]:
                            continue
                        if "email" in inp_name or "phone" in inp_name:
                            continue
                        if "email" in inp_autocomplete:
                            continue
                        
                        text_inputs.append(inp)
                    except:
                        continue
                
                if len(text_inputs) >= 2:
                    first_name_input = text_inputs[0]
                    last_name_input = text_inputs[1]
                    self._log(f"使用通用方法找到 {len(text_inputs)} 个文本输入框")
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
            from src.duckmail_client import LinkExtractor
        except ImportError:
            from duckmail_client import LinkExtractor
        
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
        
        # 切回主框架
        try:
            self.driver.switch_to.default_content()
        except:
            pass
        
        # 打开确认链接
        self._log(f"打开确认链接: {confirmation_link[:80]}...")
        self.driver.get(confirmation_link)
        
        # 等待页面加载
        WebDriverWait(self.driver, AutoRegisterConfig.PAGE_WAIT_TIMEOUT).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )
        
        time.sleep(2)
        
        # 检查是否需要切换到 iframe
        try:
            iframes = self.driver.find_elements(By.CSS_SELECTOR, "iframe[src*='login.chayns.net']")
            if iframes:
                self.driver.switch_to.frame(iframes[0])
                self._log("切换到登录 iframe")
        except:
            pass
        
        # 查找密码输入框
        self._log("查找密码输入框...")
        password_inputs = self._find_password_inputs()
        
        if len(password_inputs) == 0:
            # 可能页面还在加载，等待一下再试
            time.sleep(3)
            password_inputs = self._find_password_inputs()
        
        if len(password_inputs) == 0:
            self._dump_debug_info("未找到密码输入框")
            raise AssertionFailedException("未找到密码输入框", self.state.value)
        
        self._log(f"找到 {len(password_inputs)} 个密码输入框")
        
        # 填写密码
        if len(password_inputs) >= 2:
            # 两个密码框：密码 + 确认密码
            password_inputs[0].clear()
            password_inputs[0].send_keys(self.password)
            self._log("输入密码 (第1个框)")
            
            time.sleep(0.5)
            
            password_inputs[1].clear()
            password_inputs[1].send_keys(self.password)
            self._log("输入确认密码 (第2个框)")
        else:
            # 只有一个密码框
            password_inputs[0].clear()
            password_inputs[0].send_keys(self.password)
            self._log("输入密码")
        
        time.sleep(1)
        
        # 查找并点击设置密码按钮
        set_password_btn = self._find_set_password_button()
        
        if not set_password_btn:
            # 尝试点击 submit 按钮
            self._click_continue_button()
        else:
            self.driver.execute_script("arguments[0].click();", set_password_btn)
            self._log("点击了设置密码按钮")
        
        # 等待页面响应
        time.sleep(3)
        
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
        WebDriverWait(self.driver,Config.PAGE_WAIT_TIMEOUT).until(
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