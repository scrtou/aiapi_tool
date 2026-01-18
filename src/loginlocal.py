from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import json
import os
import re
import psutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from typing import Optional
from datetime import datetime
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

def dump_visible_inputs(driver, label=""):
    inputs = driver.find_elements(By.CSS_SELECTOR, "input")
    visible = [i for i in inputs if i.is_displayed()]
    log_message(f"{label} 可见输入框数量: {len(visible)}")
    for i in visible:
        log_message(
            f"type={i.get_attribute('type')} name={i.get_attribute('name')} "
            f"autocomplete={i.get_attribute('autocomplete')} outerHTML={i.get_attribute('outerHTML')[:160]}"
        )

def dump_body_text(driver, label=""):
    try:
        txt = driver.execute_script("return document.body && document.body.innerText ? document.body.innerText : ''")
        log_message(f"{label} bodyText前300字: {txt[:300].replace('\\n',' | ')}")
    except Exception as e:
        log_message(f"{label} 读取bodyText失败: {e}")

def wait_password_input(driver, timeout=25):
    def pick(d):
        selectors = [
            "input[type='password']",
            "input[autocomplete='current-password']",
            "input[name='password']",
            "input[name*='pass']",
            "input[id*='pass']",
        ]
        for sel in selectors:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed() and el.is_enabled():
                    return el
        return None
    return WebDriverWait(driver, timeout).until(pick)

def safe_click(driver, el):
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)

def click_next(driver, timeout=20):
    # 1) 优先 submit
    def find_submit(d):
        for b in d.find_elements(By.CSS_SELECTOR, "button[type='submit']"):
            if b.is_displayed() and b.is_enabled():
                return b
        return None

    btn = WebDriverWait(driver, timeout).until(lambda d: find_submit(d) or True)
    if btn and btn is not True:
        driver.execute_script("arguments[0].click();", btn)
        return

    # 2) 兜底：按文案（多语言）
    texts = ("Continue", "Weiter", "Next", "Fortfahren")
    def find_text(d):
        for b in d.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
            if not b.is_displayed() or not b.is_enabled():
                continue
            t = (b.text or "").strip()
            if any(x in t for x in texts):
                return b
        return None

    btn = WebDriverWait(driver, timeout).until(find_text)
    safe_click(driver, btn)

def dump_visible_buttons(driver, label=""):
    btns = driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
    log_message(f"{label} 可见按钮数量: {sum(1 for b in btns if b.is_displayed())}")
    for b in btns:
        if b.is_displayed():
            txt = (b.text or "").strip()
            log_message(f"按钮文本='{txt}'  disabled={b.get_attribute('disabled')}  outerHTML={b.get_attribute('outerHTML')[:200]}")

def find_and_click_next(driver, timeout=20):
    # 1) 先找可见的 submit 按钮（最稳）
    try:
        btn = WebDriverWait(driver, timeout).until(
            lambda d: next(
                (b for b in d.find_elements(By.CSS_SELECTOR, "button[type='submit']")
                 if b.is_displayed() and b.is_enabled()),
                None
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        return
    except TimeoutException:
        pass

    # 2) 再兜底：找文本里包含 Weiter / Continue / Next 的可见按钮
    texts = ("Weiter", "Continue", "Next", "Fortfahren")
    def pick(d):
        for b in d.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
            if not b.is_displayed():
                continue
            t = (b.text or "").strip()
            if any(x in t for x in texts) and b.is_enabled():
                return b
        return None

    btn = WebDriverWait(driver, timeout).until(pick)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    driver.execute_script("arguments[0].click();", btn)

def log_message(message):
    """打印带时间戳的日志消息"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

app = FastAPI(title="Chayns Login Service")

class ChaynsLoginRequest(BaseModel):
    username: str
    password: str

class ChaynsLoginResponse(BaseModel):
    email: str
    userid: int
    personid: str
    token: str

class ErrorResponse(BaseModel):
    error: str

class WebDriverManager:
    _instance = None
    _driver = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WebDriverManager()
        return cls._instance
    
    def __init__(self):
        self._service = None
        self._options = None
    
    def _clear_browser_data(self):
        """清理浏览器数据"""
        if self._driver:
            try:
                # 删除所有cookies
                self._driver.delete_all_cookies()
                
                # 执行更多的清理命令
                clear_scripts = [
                    "window.localStorage.clear();",
                    "window.sessionStorage.clear();",
                    "window.caches && caches.keys().then(keys => keys.forEach(key => caches.delete(key)));",
                    "window.indexedDB && indexedDB.databases().then(dbs => dbs.forEach(db => indexedDB.deleteDatabase(db.name)));",
                ]
                
                for script in clear_scripts:
                    try:
                        self._driver.execute_script(script)
                    except:
                        pass
                        
                # 强制刷新页面，绕过缓存
                self._driver.execute_script("window.location.reload(true);")
                return True
            except Exception as e:
                log_message(f"清理浏览器数据失败: {str(e)}")
                # 如果清理失败，返回False以触发重新创建driver
                return False
        return False
        
    def _create_driver(self):
        """创建新的WebDriver实例"""
        if self._service is None:
            self._service = get_chrome_driver()
        if self._options is None:
            self._options = get_chrome_options()
            
        try:
            return webdriver.Chrome(service=self._service, options=self._options)
        except Exception as e:
            log_message(f"创建driver失败: {str(e)}")
            return None
    
    def get_driver(self, clear_data=True):
        """获取WebDriver实例，如果不存在或已关闭则创建新的
        
        Args:
            clear_data (bool): 是否清理浏览器数据
        """
        try:
            if self._driver is None:
                self._driver = self._create_driver()
            else:
                try:
                    if clear_data:
                        # 如果清理失败，强制重新创建driver
                        if not self._clear_browser_data():
                            log_message("清理缓存失败，重新创建driver")
                            self.quit_driver()
                            self._driver = self._create_driver()
                except:
                    log_message("当前driver已失效，重新创建")
                    self.quit_driver()
                    self._driver = self._create_driver()
            return self._driver
        except Exception as e:
            log_message(f"获取driver时出错: {str(e)}")
            return None
    
    def quit_driver(self):
        """安全关闭driver"""
        if self._driver:
            try:
                self._driver.quit()
            except:
                pass
            finally:
                self._driver = None

def kill_chrome_processes():
    """终止所有Chrome相关进程"""
    for proc in psutil.process_iter():
        try:
            if "chrome" in proc.name().lower():
                proc.kill()
        except:
            pass

def get_chrome_driver():
    """获取ChromeDriver，优先使用本地安装的版本"""
    try:
        # 清理可能存在的Chrome进程
        os.system("pkill -f chrome")
        time.sleep(1)
        
        # 清理旧的Chrome数据目录
        os.system("rm -rf /tmp/chrome-data-*")
        
        if os.path.exists('/usr/bin/chromedriver'):
            service = Service('/usr/bin/chromedriver')
            # 测试service是否可用
            driver = webdriver.Chrome(service=service, options=get_chrome_options())
            driver.quit()
            return service
        elif os.path.exists('/usr/local/bin/chromedriver'):
            service = Service('/usr/local/bin/chromedriver')
            # 测试service是否可用
            driver = webdriver.Chrome(service=service, options=get_chrome_options())
            driver.quit()
            return service
        else:
            try:
                log_message("ChromeDriver安装中...")
                return Service(ChromeDriverManager().install())
            except Exception as e:
                log_message(f"ChromeDriver安装失败: {str(e)}")
                raise
    except Exception as e:
        log_message(f"ChromeDriver加载失败: {str(e)}")
        raise

def get_chrome_options():
    chrome_options = Options()

    user_data_dir = f"/tmp/chrome-data-{time.time()}"
    os.makedirs(user_data_dir, exist_ok=True)
    os.chmod(user_data_dir, 0o777)
    chrome_options.add_argument(f'--user-data-dir={user_data_dir}')

    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')

    # 先不要 incognito + 不要禁 cookies
    # chrome_options.add_argument('--incognito')  # 先注释掉

    # 先别禁用 NetworkService / 不要一堆 disable-features
    # chrome_options.add_argument('--disable-features=NetworkService')  # 先注释掉

    # 不设置 cookie block prefs（先跑通）
    # chrome_options.add_experimental_option('prefs', prefs)  # 先去掉

    return chrome_options


def check_login_status(driver):
    """检查登录状态"""
    try:
        # 检查URL是否包含特定的登录成功标识
        current_url = driver.current_url
        #print(f"当前URL: {current_url}")
        
        # 尝试获取用户信息元素
        try:
            # 等待页面加载完成
            WebDriverWait(driver, 10).until(
                lambda x: x.execute_script("return document.readyState") == "complete"
            )
            
            # 打印页面标题
            log_message(f"页面标题: {driver.title}")
            
            # 获取localStorage
            local_storage = driver.execute_script("return window.localStorage;")
            #print("LocalStorage:", local_storage)
            
            # 获取所有cookies
            #cookies = driver.get_cookies()
            #print("Cookies:", cookies)
            
        except Exception as e:
            log_message(f"获取页面信息失败: {str(e)}")
        
        return True
    except Exception as e:
        log_message(f"检查登录状态失败: {str(e)}")
        return False

def login_chayns(username, password):
    """登录Chayns并获取用户信息"""
    driver_manager = WebDriverManager.get_instance()
    driver = None
    try:
        log_message("正在获取浏览器实例...")
        start_time = time.time()
        # 获取driver时清理浏览器数据
        driver = driver_manager.get_driver(clear_data=True)
        if not driver:
            raise Exception("无法创建浏览器实例")
        end_time = time.time()
        log_message(f"浏览器准备时间: {end_time - start_time} 秒")
        
        log_message("正在访问网站...")
        #登录页面https://chayns.de/id
        driver.get("https://chayns.de")

        log_message("等待页面加载...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # 添加延迟确保页面完全加载
        time.sleep(2)
        
        log_message("正在尝试定位登录按钮...")
        log_message(f"页面标题: {driver.title}")
        log_message(f"当前URL: {driver.current_url}")
        
        #多行注释
        '''
        # 打印页面源码用于调试
        log_message(f"页面源码: {driver.page_source[:1000]}")  # 只打印前1000个字符
        
        # 尝试查找所有按钮元素
        buttons = driver.find_elements(By.TAG_NAME, "button")
        log_message(f"找到 {len(buttons)} 个按钮元素")
        for button in buttons:
            log_message(f"按钮文本: {button.text}")
            log_message(f"按钮类名: {button.get_attribute('class')}")
        '''
        try:
            # 先等待页面上任何按钮元素出现
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "button"))
            )
            
            # 然后尝试多种方式查找登录按钮,先通过css选择器
            try:
                # 尝试通过按钮文本找到"Anmelden"按钮
                login_button = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button.beta-chayns-button"))
                    )
                log_message("找到登录按钮 (通过beta-chayns-button类)")
                
            except:
                try:
                    login_button = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Anmelden')]"))
                    )
                    log_message("找到登录按钮 (通过Anmelden文本)")
                except:
                    raise Exception("没有找到任何按钮")
            
            # 确保按钮可以点击
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.TAG_NAME, "button"))
            )
            
            # 使用JavaScript点击按钮
            driver.execute_script("arguments[0].click();", login_button)
            log_message("成功点击登录按钮")
            
        except Exception as e:
            log_message(f"无法找到或点击登录按钮: {str(e)}")
            raise
        
        # 等待登录iframe加载
        #/html/body/div[1]/div/div[1]/div/div[2]/div[2]/div/div/div[2]
        #先判断是否有.这个元素div.last-login-user-item:nth-child(2),有click事件
        
        WebDriverWait(driver,20).until(
            EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[src*='login.chayns.net']"))
        )
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "/html/body/div[1]/div/div[1]/div/div[2]/div[2]/div/div/div[2]"))
            )
            log_message("存在other-user元素")
            #获取，点击
            other_user = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "/html/body/div[1]/div/div[1]/div/div[2]/div[2]/div/div/div[2]"))
            )
            other_user.click()
        except:
            log_message("不存在other-user元素")
        
        # 等待邮箱输入框出现并输入
        try:
            username_input = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[name="email-phone"]'))
            )
            username_input.clear()
            username_input.send_keys(username)
            username_input.send_keys(Keys.TAB)  # 触发 blur/change
            log_message("输入邮箱")
        except Exception as e:
            log_message(f"输入邮箱时出错: {str(e)}")
            return None

        # 点击“下一步/Continue”
        time.sleep(1)
        try:
            dump_visible_buttons(driver, "输入邮箱后")
            click_next(driver, timeout=20)
            log_message("已点击邮箱步骤的下一步按钮")
        except Exception as e:
            log_message(f"点击邮箱按钮时出错: {str(e)}")
            return None

        # 等待密码输入框出现并输入
        time.sleep(1)
        # 点 Continue 后，等待邮箱输入框消失（进入下一步的强信号）
        try:
            WebDriverWait(driver, 10).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, "input[name='email-phone']"))
            )
        except TimeoutException:
            log_message("Continue后邮箱输入框仍然存在：没有进入下一步")
            dump_login_errors(driver, "Continue未推进时")
            return None
        try:
            password_input = wait_password_input(driver, timeout=25)
            password_input.clear()
            password_input.send_keys(password)
            password_input.send_keys(Keys.TAB)
            log_message("输入密码")
        except Exception as e:
            log_message(f"输入密码时出错: {str(e)}")
            return None

        # 点击提交/下一步
        time.sleep(1)
        try:
            click_next(driver, timeout=20)
            log_message("已点击密码步骤的下一步按钮")
        except Exception as e:
            log_message(f"点击提交按钮时出错: {str(e)}")
            return None
        
        # 切回主框架
        time.sleep(1)
        driver.switch_to.default_content()
        
        # 在获取数据之前检查登录状态
        if not check_login_status(driver):
            log_message("登录状态检查失败")
            return None
        
        log_message("登录成功！")
        
        # 等待页面完全加载和JavaScript执行
        WebDriverWait(driver, 20).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )
        
        # 等待Cookies中有一个"at_"或者30秒超时
        WebDriverWait(driver, 30).until(
            lambda d: any(cookie['name'].startswith('at_') for cookie in d.get_cookies())
        )
        
        # 获取该cookies的value
        #获取at_开头的name
        at_xxx_cookie = None
        for cookie in driver.get_cookies():
            if cookie['name'].startswith('at_'):
                at_xxx_cookie = cookie
                break
        
        if at_xxx_cookie is None:
            log_message("未找到at_xxx cookie")
            return None
        
        data = {}
        data["token"] = at_xxx_cookie["value"]
        # 等待 window.cwInfo 对象出现, 该对象在登录后的页面中
        try:
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return typeof window.cwInfo !== 'undefined' && window.cwInfo.user;")
            )
        except Exception as e:
            log_message(f"等待 window.cwInfo 对象超时: {e}")
            log_message(f"当前 URL: {driver.current_url}")
            log_message(f"页面标题: {driver.title}")
            return None

        # 直接从JavaScript获取用户信息对象
        user_info = driver.execute_script("return window.cwInfo;")

        if not user_info or "user" not in user_info or "personId" not in user_info["user"] or "id" not in user_info["user"]:
             log_message("用户信息不完整。")
             log_message(f"找到的用户信息: {user_info.get('user')}")
             return None
        data["personid"] = str(user_info["user"]["personId"])
        data["userid"] = int(user_info["user"]["id"])
        data["email"] = username
        log_message(f"data: {data}")
        return data
    except Exception as e:
        log_message(f"获取用户信息时出现错误: {str(e)}")
        return None

@app.post("/aichat/chayns/login", response_model=ChaynsLoginResponse, responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
async def handle_login(request: ChaynsLoginRequest):
    try:
        log_message(f"收到登录请求: username={request.username},passwd={request.password}")
        user_data = login_chayns(request.username, request.password)
        
        if user_data:
            return ChaynsLoginResponse(**user_data)
        else:
            raise HTTPException(status_code=401, detail="登录失败")
            
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# 使用启动事件处理初始化
@app.on_event("startup")
async def startup_event():
    log_message("启动登录服务")
    log_message("清理环境...")
    os.system("pkill -f chrome")
    os.system("rm -rf /tmp/chrome-data-*")
    time.sleep(2)

    log_message("启动浏览器")
    try:
        start_time = time.time()
        driver_manager = WebDriverManager.get_instance()
        driver = driver_manager.get_driver(clear_data=True)
        if not driver:
            raise Exception("浏览器启动失败")
        end_time = time.time()
        log_message(f"启动浏览器成功: {end_time - start_time} 秒")
    except Exception as e:
        log_message(f"启动浏览器失败: {str(e)}")
        raise

# 如果直接运行Python文件,则使用这个入口
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5555, log_level="info")