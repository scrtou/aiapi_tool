from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.remote.webdriver import WebDriver
from webdriver_manager.chrome import ChromeDriverManager
import time
import json
import os
import psutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from typing import Optional
import requests
from urllib3.util.retry import Retry  # 添加这行
from requests.adapters import HTTPAdapter


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
    
    def _get_chrome_options(self):
        """配置Chrome选项"""
        chrome_options = Options()
        
        # 只保留最必要的选项
        chrome_options.add_argument('--headless=new')  # 使用新版headless模式
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        #禁用更新
        chrome_options.add_argument('--disable-updates')
        chrome_options.add_argument('--disable-crash-reporter')
        chrome_options.add_argument('--disable-background-networking')
        chrome_options.add_argument('--disable-sync')
        chrome_options.add_argument('--disable-translate')
        #无痕，隐私
        chrome_options.add_argument('--incognito')
        # 添加性能优化选项
        chrome_options.add_argument('--disable-extensions')  # 禁用扩展
        chrome_options.add_argument('--disable-gpu')  # 禁用GPU加速
        chrome_options.add_argument('--disable-software-rasterizer')  # 禁用软件光栅化
        chrome_options.add_argument('--disable-features=NetworkService')  # 禁用网络服务
        chrome_options.add_argument('--disable-dev-tools')  # 禁用开发者工具
        chrome_options.add_argument('--no-first-run')  # 跳过首次运行检查
        chrome_options.add_argument('--no-default-browser-check')  # 跳过默认浏览器检查
        chrome_options.add_argument('--disable-infobars')  # 禁用信息栏
        chrome_options.add_argument('--disable-notifications')  # 禁用通知
        chrome_options.add_argument('--disable-popup-blocking')  # 禁用弹窗拦截
        chrome_options.add_argument('--ignore-certificate-errors')  # 忽略证书错误
        # 设置页面加载策略
        chrome_options.page_load_strategy = 'eager'  # 等待DOMContentLoaded事件触发即可，不等待页面完全加载
        
        # 禁用所有缓存
        chrome_options.add_argument('--disable-application-cache')
        chrome_options.add_argument('--disable-cache')
        chrome_options.add_argument('--disable-offline-load-stale-cache')
        chrome_options.add_argument('--disk-cache-size=0')
        
        # 禁用各种功能以提高性能和隐私
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-sync')
        chrome_options.add_argument('--disable-translate')
        chrome_options.add_argument('--disable-notifications')
        chrome_options.add_argument('--disable-popup-blocking')
        
        # 设置更严格的隐私选项
        prefs = {
            'profile.default_content_setting_values': {
                'cookies': 2,  # 2表示阻止所有cookies
                'images': 1,
                'javascript': 1,
                'plugins': 2,
                'popups': 2,
                'geolocation': 2,
                'notifications': 2,
                'auto_select_certificate': 2,
                'fullscreen': 2,
                'mouselock': 2,
                'mixed_script': 2,
                'media_stream': 2,
                'media_stream_mic': 2,
                'media_stream_camera': 2,
                'protocol_handlers': 2,
                'ppapi_broker': 2,
                'automatic_downloads': 2,
                'midi_sysex': 2,
                'push_messaging': 2,
                'ssl_cert_decisions': 2,
                'metro_switch_to_desktop': 2,
                'protected_media_identifier': 2,
                'app_banner': 2,
                'site_engagement': 2,
                'durable_storage': 2
            },
            'profile.managed_default_content_settings': {
                'cookies': 1  # 允许必要的cookies用于登录
            }
        }
        chrome_options.add_experimental_option('prefs', prefs)
            
        return chrome_options
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WebDriverManager()
        return cls._instance
    
    def __init__(self):
        self._command_executor = 'http://localhost:4444/wd/hub'
        #self._command_executor = 'http://chrome:4444/wd/hub'
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
                print(f"清理浏览器数据失败: {str(e)}")
                # 如果清理失败，返回False以触发重新创建driver
                return False
        return False
        
    def _wait_for_selenium_grid(self, timeout=60):
        """等待Selenium Grid就绪"""
        selenium_host = os.getenv('SELENIUM_HOST', 'chrome')
        selenium_port = os.getenv('SELENIUM_PORT', '4444')
        url = f"http://{selenium_host}:{selenium_port}/wd/hub/status"
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 配置重试策略
                session = requests.Session()
                retries = Retry(total=3, backoff_factor=0.5)
                session.mount('http://', HTTPAdapter(max_retries=retries))
                
                response = session.get(url, timeout=5)
                if response.status_code == 200:
                    print("Selenium Grid 已就绪")
                    return True
            except requests.RequestException as e:
                print(f"等待Selenium Grid: {str(e)}")
            time.sleep(5)
        
        raise Exception(f"Selenium Grid 在 {timeout} 秒内未就绪")
    
    def _create_driver(self, max_retries=3) -> webdriver.Remote:
        """创建远程WebDriver实例"""
        retry_count = 0
        last_exception = None
        
        while retry_count < max_retries:
            try:
                # 等待Selenium Grid就绪
                self._wait_for_selenium_grid()
                
                chrome_options = self._get_chrome_options()
                selenium_host = os.getenv('SELENIUM_HOST', 'chrome')
                selenium_port = os.getenv('SELENIUM_PORT', '4444')
                remote_url = f"http://{selenium_host}:{selenium_port}/wd/hub"
                
                print(f"尝试连接到Selenium服务器 (第{retry_count + 1}次): {remote_url}")
                
                driver = webdriver.Remote(
                    command_executor=remote_url,
                    options=chrome_options
                )
                
                # 验证driver是否正常工作
                driver.current_url
                print("WebDriver创建成功")
                return driver
                
            except Exception as e:
                last_exception = e
                retry_count += 1
                print(f"创建WebDriver失败 (尝试 {retry_count}/{max_retries}): {str(e)}")
                
                if retry_count < max_retries:
                    time.sleep(5 * retry_count)  # 递增等待时间
                    continue
                    
                raise Exception(f"创建WebDriver失败，已重试{max_retries}次: {str(last_exception)}")
    
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
                    self._driver.current_url
                    if clear_data:
                        # 如果清理失败，强制重新创建driver
                        if not self._clear_browser_data():
                            print("清理缓存失败，重新创建driver")
                            self.quit_driver()
                            self._driver = self._create_driver()
                except:
                    print("当前driver已失效，重新创建")
                    self.quit_driver()
                    self._driver = self._create_driver()
            return self._driver
        except Exception as e:
            print(f"获取driver时出错: {str(e)}")
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
            print(f"页面标题: {driver.title}")
            
            # 获取localStorage
            local_storage = driver.execute_script("return window.localStorage;")
            #print("LocalStorage:", local_storage)
            
            # 获取所有cookies
            #cookies = driver.get_cookies()
            #print("Cookies:", cookies)
            
        except Exception as e:
            print(f"获取页面信息失败: {str(e)}")
        
        return True
    except Exception as e:
        print(f"检查登录状态失败: {str(e)}")
        return False
def check_selenium_connection():
    """检查Selenium连接"""
    selenium_host = os.getenv('SELENIUM_HOST', 'chrome')
    selenium_port = os.getenv('SELENIUM_PORT', '4444')
    
    print(f"检查Selenium连接 ({selenium_host}:{selenium_port})...")
    
    try:
        response = requests.get(f"http://{selenium_host}:{selenium_port}/wd/hub/status")
        if response.status_code == 200:
            print("Selenium服务器可用")
            return True
    except requests.RequestException as e:
        print(f"Selenium服务器不可用: {str(e)}")
    return False
def login_chayns(username, password):
     """登录Chayns并获取用户信息"""
    driver_manager = WebDriverManager.get_instance()
    driver = None
    try:
        print("正在获取浏览器实例...")
        start_time = time.time()
        # 获取driver时清理浏览器数据
        driver = driver_manager.get_driver(clear_data=True)
        if not driver:
            raise Exception("无法创建浏览器实例")
        end_time = time.time()
        print(f"浏览器准备时间: {end_time - start_time} 秒")
        
        print("正在访问网站...")
        #登录页面https://chayns.de/id
        driver.get("https://chayns.de")

        print("等待页面加载...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # 添加延迟确保页面完全加载
        time.sleep(2)
        
        print("正在尝试定位登录按钮...")
        print("页面标题:", driver.title)
        print("当前URL:", driver.current_url)
        
        #多行注释
        '''
        # 打印页面源码用于调试
        print("页面源码:", driver.page_source[:1000])  # 只打印前1000个字符
        
        # 尝试查找所有按钮元素
        buttons = driver.find_elements(By.TAG_NAME, "button")
        print(f"找到 {len(buttons)} 个按钮元素")
        for button in buttons:
            print(f"按钮文本: {button.text}")
            print(f"按钮类名: {button.get_attribute('class')}")
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
                print("找到登录按钮 (通过beta-chayns-button类)")
                
            except:
                try:
                    login_button = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Anmelden')]"))
                    )
                    print("找到登录按钮 (通过Anmelden文本)")
                except:
                    raise Exception("没有找到任何按钮")
            
            # 确保按钮可以点击
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.TAG_NAME, "button"))
            )
            
            # 使用JavaScript点击按钮
            driver.execute_script("arguments[0].click();", login_button)
            print("成功点击登录按钮")
            
        except Exception as e:
            print(f"无法找到或点击登录按钮: {str(e)}")
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
            print("存在other-user元素")
            #获取，点击
            other_user = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "/html/body/div[1]/div/div[1]/div/div[2]/div[2]/div/div/div[2]"))
            )
            other_user.click()
        except:
            print("不存在other-user元素")
        
        # 等待邮箱输入框出现并输入
        try:
            username_input = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#CC_INPUT_0"))
            )
            username_input.send_keys(username)
            print("输入邮箱")
        except Exception as e:
            print(f"输入邮箱时出错: {str(e)}")
            return None
        
        # 点击button
        time.sleep(1)
        try:
            button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".form__email__wrapper__button"))
            )
            button.click()
        except Exception as e:
            print(f"点击邮箱按钮时出错: {str(e)}")
            return None
        
        # 等待密码输入框出现并输入
        time.sleep(1)

        try:
            password_input = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#CC_INPUT_3"))
            )
            password_input.send_keys(password)
            print("输入密码")
        except Exception as e:
            print(f"输入密码时出错: {str(e)}")
            return None
        
        # 点击提交按钮
        time.sleep(1)

        try:
            submit_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".form__password-wrapper__button"))
            )
            submit_button.click()
        except Exception as e:
            print(f"点击提交按钮时出错: {str(e)}")
            return None
        
        # 切回主框架
        time.sleep(1)
        driver.switch_to.default_content()
        
        # 在获取数据之前检查登录状态
        if not check_login_status(driver):
            print("登录状态检查失败")
            return None
        
        print("登录成功！")
        
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
            print("未找到at_xxx cookie")
            return None
        
        data = {}
        data["token"] = at_xxx_cookie["value"]
        #print("at_xxx:", at_xxx)
        
        # 用at_xxx作为鉴权头访问https://chayns.de/id
        driver.get("https://chayns.de/id")
        # 等待页面完全加载
        WebDriverWait(driver, 20).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )
        
        # 获取access token
        access_token_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='hidden']"))
        )
        access_token = access_token_input.get_attribute("value")
       #转换为json
        user_data = json.loads(access_token)
        data["personid"] = str(user_data["user"]["personId"])
        data["userid"] = int(user_data["user"]["userId"])
        data["email"] = username;
        print("data:", data)
        return data
    except Exception as e:
        print(f"获取用户信息时出现错误: {str(e)}")
        return None

@app.post("/aichat/chayns/login", response_model=ChaynsLoginResponse, responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
async def handle_login(request: ChaynsLoginRequest):
    try:
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

if __name__ == "__main__":
    print("启动登录服务")
    
    # 等待Selenium服务就绪
    max_retries = 10
    retry_count = 0
    
    while retry_count < max_retries:
        if check_selenium_connection():
            break
        retry_count += 1
        print(f"等待Selenium服务就绪 ({retry_count}/{max_retries})...")
        time.sleep(5)
    
    if retry_count >= max_retries:
        print("无法连接到Selenium服务器，退出程序")
        os._exit(1)
    
    print("启动浏览器...")
    try:
        driver_manager = WebDriverManager.get_instance()
        driver = driver_manager.get_driver(clear_data=True)
        if driver:
            print("浏览器启动成功")
            uvicorn.run(app, host="0.0.0.0", port=5556, log_level="info")
        else:
            print("浏览器启动失败")
            os._exit(1)
    except Exception as e:
        print(f"启动失败: {str(e)}")
        os._exit(1)
