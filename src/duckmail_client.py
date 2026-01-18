"""
DuckMail 客户端模块

负责：
- 创建 DuckMail 账户
- 获取 token
- 轮询 messages 列表，命中验证邮件
- 拉取邮件正文（html/text）并提取验证链接（含 ccUrl 解码策略）

参考设计文档：auto_regist/autoregister_design.md
"""

import re
import time
import secrets
import string
import requests
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, parse_qs, unquote
from dataclasses import dataclass
from datetime import datetime


# ============== 配置 ==============
DUCKMAIL_BASE_URL = "https://api.duckmail.sbs"
DEFAULT_DOMAIN = "duckmail.sbs"
DEFAULT_POLL_TIMEOUT = 120  # 总超时秒数
DEFAULT_POLL_INTERVAL = 2   # 轮询间隔秒数

# 验证邮件主题匹配关键字（正则）
DEFAULT_SUBJECT_PATTERNS = [
    r"Welcome to chayns",
    r"verify",
    r"activate",
    r"confirm",
    r"Willkommen",
    r"bestätigen",
]

# 验证邮件发件人白名单
VERIFICATION_SENDERS = [
    "noreply@chayns.de",
    "no-reply@chayns.de",
]


# ============== 日志工具 ==============
def log_message(message: str):
    """打印带时间戳的日志消息"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [DuckMail] {message}")


# ============== 数据模型 ==============
@dataclass
class DuckMailAccount:
    """DuckMail 账户信息"""
    address: str
    password: str
    account_id: Optional[str] = None
    token: Optional[str] = None


@dataclass
class EmailMessage:
    """邮件消息摘要"""
    id: str
    subject: str
    from_address: str
    from_name: str
    created_at: str
    seen: bool = False


@dataclass
class EmailDetail:
    """邮件详情"""
    id: str
    subject: str
    from_address: str
    text: str
    html: List[str]


# ============== DuckMail 客户端 ==============
class DuckMailClient:
    """DuckMail API 客户端"""
    
    def __init__(self, base_url: str = DUCKMAIL_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.account: Optional[DuckMailAccount] = None
    
    def _headers(self, with_auth: bool = True) -> Dict[str, str]:
        """构造请求头"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if with_auth and self.account and self.account.token:
            headers["Authorization"] = f"Bearer {self.account.token}"
        return headers
    
    @staticmethod
    def generate_email_prefix(length: int = 10) -> str:
        """生成随机邮箱前缀"""
        chars = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(length))
    
    @staticmethod
    def generate_password(length: int = 16) -> str:
        """生成随机密码"""
        chars = string.ascii_letters + string.digits + "!@#$%"
        return ''.join(secrets.choice(chars) for _ in range(length))
    
    def create_account(
        self,
        email_prefix: Optional[str] = None,
        domain: str = DEFAULT_DOMAIN,
        password: Optional[str] = None
    ) -> DuckMailAccount:
        """
        创建 DuckMail 账户
        
        POST /accounts
        
        Args:
            email_prefix: 邮箱前缀，不传则自动生成
            domain: 邮箱域名，默认 duckmail.sbs
            password: 账户密码，不传则自动生成
        
        Returns:
            DuckMailAccount 对象
        """
        if not email_prefix:
            email_prefix = self.generate_email_prefix()
        if not password:
            password = self.generate_password()
        
        address = f"{email_prefix}@{domain}"
        
        log_message(f"创建 DuckMail 账户: {address}")
        
        payload = {
            "address": address,
            "password": password
        }
        
        try:
            resp = self.session.post(
                f"{self.base_url}/accounts",
                json=payload,
                headers=self._headers(with_auth=False),
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            
            account_id = data.get("id") or data.get("@id", "").split("/")[-1]
            
            self.account = DuckMailAccount(
                address=address,
                password=password,
                account_id=account_id
            )
            
            log_message(f"账户创建成功: {address}, account_id={account_id}")
            return self.account
            
        except requests.exceptions.HTTPError as e:
            log_message(f"创建账户失败: HTTP {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            log_message(f"创建账户失败: {str(e)}")
            raise
    
    def get_token(self, address: Optional[str] = None, password: Optional[str] = None) -> str:
        """
        获取 DuckMail 访问 token
        
        POST /token
        
        Args:
            address: 邮箱地址，不传则使用当前账户
            password: 密码，不传则使用当前账户
        
        Returns:
            JWT token 字符串
        """
        if not address:
            if not self.account:
                raise ValueError("未创建账户，请先调用 create_account()")
            address = self.account.address
            password = self.account.password
        
        log_message(f"获取 token: {address}")
        
        payload = {
            "address": address,
            "password": password
        }
        
        try:
            resp = self.session.post(
                f"{self.base_url}/token",
                json=payload,
                headers=self._headers(with_auth=False),
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            
            token = data.get("token")
            if not token:
                raise ValueError(f"响应中未找到 token 字段: {data}")
            
            if self.account:
                self.account.token = token
            
            log_message(f"获取 token 成功: {token[:20]}...")
            return token
            
        except requests.exceptions.HTTPError as e:
            log_message(f"获取 token 失败: HTTP {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            log_message(f"获取 token 失败: {str(e)}")
            raise
    
    def list_messages(self) -> List[EmailMessage]:
        """
        获取邮件列表
        
        GET /messages
        
        Returns:
            EmailMessage 列表，按 createdAt 倒序排列
        """
        if not self.account or not self.account.token:
            raise ValueError("未获取 token，请先调用 get_token()")
        
        log_message("获取邮件列表")
        
        try:
            resp = self.session.get(
                f"{self.base_url}/messages",
                headers=self._headers(),
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            
            # 解析 hydra:member 格式
            members = data.get("hydra:member", [])
            
            messages = []
            for m in members:
                from_info = m.get("from", {})
                msg = EmailMessage(
                    id=m.get("id", ""),
                    subject=m.get("subject", ""),
                    from_address=from_info.get("address", ""),
                    from_name=from_info.get("name", ""),
                    created_at=m.get("createdAt", ""),
                    seen=m.get("seen", False)
                )
                messages.append(msg)
            
            # 按 createdAt 倒序排列（最新的在前）
            messages.sort(key=lambda x: x.created_at, reverse=True)
            
            log_message(f"获取到 {len(messages)} 封邮件")
            return messages
            
        except requests.exceptions.HTTPError as e:
            log_message(f"获取邮件列表失败: HTTP {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            log_message(f"获取邮件列表失败: {str(e)}")
            raise
    
    def get_message(self, message_id: str) -> EmailDetail:
        """
        获取邮件详情
        
        GET /messages/{id}
        
        Args:
            message_id: 邮件 ID
        
        Returns:
            EmailDetail 对象
        """
        if not self.account or not self.account.token:
            raise ValueError("未获取 token，请先调用 get_token()")
        
        log_message(f"获取邮件详情: {message_id}")
        
        try:
            resp = self.session.get(
                f"{self.base_url}/messages/{message_id}",
                headers=self._headers(),
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            
            from_info = data.get("from", {})
            html_list = data.get("html", [])
            if isinstance(html_list, str):
                html_list = [html_list]
            
            detail = EmailDetail(
                id=data.get("id", ""),
                subject=data.get("subject", ""),
                from_address=from_info.get("address", ""),
                text=data.get("text", ""),
                html=html_list
            )
            
            log_message(f"邮件详情获取成功: subject='{detail.subject}'")
            return detail
            
        except requests.exceptions.HTTPError as e:
            log_message(f"获取邮件详情失败: HTTP {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            log_message(f"获取邮件详情失败: {str(e)}")
            raise
    
    def is_verification_email(
        self,
        message: EmailMessage,
        subject_patterns: Optional[List[str]] = None,
        sender_whitelist: Optional[List[str]] = None
    ) -> bool:
        """
        判断是否为验证邮件
        
        Args:
            message: 邮件摘要
            subject_patterns: 主题匹配正则列表
            sender_whitelist: 发件人白名单
        
        Returns:
            是否为验证邮件
        """
        if subject_patterns is None:
            subject_patterns = DEFAULT_SUBJECT_PATTERNS
        if sender_whitelist is None:
            sender_whitelist = VERIFICATION_SENDERS
        
        # 检查发件人
        if sender_whitelist:
            sender_match = message.from_address.lower() in [s.lower() for s in sender_whitelist]
            if sender_match:
                log_message(f"发件人匹配: {message.from_address}")
                return True
        
        # 检查主题
        for pattern in subject_patterns:
            if re.search(pattern, message.subject, re.IGNORECASE):
                log_message(f"主题匹配: pattern='{pattern}', subject='{message.subject}'")
                return True
        
        return False


# ============== 链接提取工具 ==============
class LinkExtractor:
    """邮件链接提取器"""
    
    @staticmethod
    def extract_hrefs_from_html(html_content: str) -> List[str]:
        """
        从 HTML 内容中提取所有 href 链接
        
        Args:
            html_content: HTML 字符串
        
        Returns:
            URL 列表
        """
        # 匹配 href="..." 或 href='...'
        pattern = r'href=["\']([^"\']+)["\']'
        matches = re.findall(pattern, html_content, re.IGNORECASE)
        return matches
    
    @staticmethod
    def extract_urls_from_text(text_content: str) -> List[str]:
        """
        从纯文本内容中提取 URL
        
        Args:
            text_content: 纯文本字符串
        
        Returns:
            URL 列表
        """
        # 匹配 http:// 或 https:// 开头的 URL
        pattern = r'https?://[^\s<>"\']+' 
        matches = re.findall(pattern, text_content)
        return matches
    
    @staticmethod
    def extract_ccurl(url: str) -> Optional[str]:
        """
        从 URL 中提取并解码 ccUrl 参数
        
        邮件按钮链接示例：https://sidekick.ki?tappAction=cc&ccUrl=<urlencoded>&nrd=1
        其中 ccUrl 是 URL 编码后的真实目标地址
        
        Args:
            url: 原始 URL
        
        Returns:
            解码后的 ccUrl，如果不存在则返回 None
        """
        try:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            cc_url_list = query_params.get("ccUrl") or query_params.get("ccurl")
            if cc_url_list:
                # URL decode 一次
                decoded = unquote(cc_url_list[0])
                log_message(f"提取到 ccUrl: {decoded[:100]}...")
                return decoded
            
            return None
        except Exception as e:
            log_message(f"提取 ccUrl 失败: {str(e)}")
            return None
    
    @staticmethod
    def is_verification_link(url: str) -> bool:
        """
        判断是否为验证链接
        
        白名单规则（基于设计文档）：
        - 包含 tappAction=cc 且带有 ccUrl= 参数
        - 或包含 chayns.cc/login1
        - 或包含 code= 参数
        
        Args:
            url: URL 字符串
        
        Returns:
            是否为验证链接
        """
        url_lower = url.lower()
        
        # 规则1：包含 tappAction=cc 且带有 ccUrl=
        if "tappaction=cc" in url_lower and "ccurl=" in url_lower:
            return True
        
        # 规则2：包含 chayns.cc/login1
        if "chayns.cc/login1" in url_lower:
            return True
        
        # 规则3：包含 code= 参数（通常是验证码）
        if "code=" in url_lower and ("chayns" in url_lower or "login" in url_lower):
            return True
        
        return False
    
    @classmethod
    def extract_confirmation_link(cls, email_detail: EmailDetail) -> Optional[str]:
        """
        从邮件详情中提取确认链接
        
        策略（按优先级）：
        1. 优先从 html[] 中提取 href
        2. 其次从 text 中提取 URL
        3. 白名单过滤：优先匹配包含 tappAction=cc + ccUrl 的链接
        4. ccUrl 解码：提取 ccUrl 参数并 URL decode 一次
        5. 如果解码失败或解码后 URL 无效，回退到原始 href
        
        Args:
            email_detail: 邮件详情对象
        
        Returns:
            最终的确认链接 URL，如果未找到则返回 None
        """
        all_urls = []
        
        # 1. 从 html[] 提取 href
        for html_content in email_detail.html:
            hrefs = cls.extract_hrefs_from_html(html_content)
            all_urls.extend(hrefs)
        
        # 2. 兜底：从 text 提取 URL
        if not all_urls:
            text_urls = cls.extract_urls_from_text(email_detail.text)
            all_urls.extend(text_urls)
        
        log_message(f"共提取到 {len(all_urls)} 个链接")
        
        # 3. 过滤：优先找验证链接
        verification_links = [u for u in all_urls if cls.is_verification_link(u)]
        
        if not verification_links:
            log_message("未找到符合白名单的验证链接")
            # 如果没有匹配白名单的，尝试找任何 http/https 链接（排除静态资源）
            verification_links = [
                u for u in all_urls 
                if u.startswith("http") and not any(
                    ext in u.lower() for ext in [".png", ".jpg", ".gif", ".css", ".js"]
                )
            ]
        
        if not verification_links:
            log_message("未找到任何可用链接")
            return None
        
        # 4. 取第一个验证链接
        original_link = verification_links[0]
        log_message(f"选中链接: {original_link[:100]}...")
        
        # 5. 尝试提取并解码 ccUrl
        cc_url = cls.extract_ccurl(original_link)
        if cc_url:
            # 验证解码后的 URL 格式是否有效
            if cc_url.startswith("http"):
                log_message(f"使用解码后的 ccUrl: {cc_url[:100]}...")
                return cc_url
            else:
                log_message(f"ccUrl 格式无效，回退到原始链接")
        
        # 6. 回退：使用原始链接
        log_message(f"使用原始链接: {original_link[:100]}...")
        return original_link


# ============== 轮询等待器 ==============
class MailPoller:
    """邮件轮询器"""
    
    def __init__(self, client: DuckMailClient):
        self.client = client
    
    def wait_for_verification_email(
        self,
        timeout_seconds: int = DEFAULT_POLL_TIMEOUT,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        subject_patterns: Optional[List[str]] = None,
        sender_whitelist: Optional[List[str]] = None
    ) -> Optional[EmailMessage]:
        """
        轮询等待验证邮件
        
        Args:
            timeout_seconds: 总超时秒数，默认 120
            poll_interval: 轮询间隔秒数，默认 2
            subject_patterns: 主题匹配正则列表
            sender_whitelist: 发件人白名单
        
        Returns:
            匹配的 EmailMessage，如果超时则返回 None
        """
        log_message(f"开始轮询验证邮件，超时={timeout_seconds}秒，间隔={poll_interval}秒")
        
        start_time = time.time()
        seen_ids = set()  # 已检查过的邮件 ID
        
        while time.time() - start_time < timeout_seconds:
            try:
                messages = self.client.list_messages()
                
                for msg in messages:
                    # 跳过已检查过的
                    if msg.id in seen_ids:
                        continue
                    
                    seen_ids.add(msg.id)
                    
                    # 检查是否为验证邮件
                    if self.client.is_verification_email(
                        msg,
                        subject_patterns=subject_patterns,
                        sender_whitelist=sender_whitelist
                    ):
                        log_message(f"找到验证邮件: id={msg.id}, subject='{msg.subject}'")
                        return msg
                
                elapsed = time.time() - start_time
                log_message(f"未找到验证邮件，已等待 {elapsed:.1f} 秒，继续轮询...")
                
            except Exception as e:
                log_message(f"轮询时出错: {str(e)}")
            
            time.sleep(poll_interval)
        
        log_message(f"轮询超时（{timeout_seconds}秒）：未收到验证邮件")
        return None
    
    def get_confirmation_link(
        self,
        timeout_seconds: int = DEFAULT_POLL_TIMEOUT,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        subject_patterns: Optional[List[str]] = None,
        sender_whitelist: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        轮询等待验证邮件并提取确认链接
        
        这是一个便捷方法，组合了：
        1. wait_for_verification_email()
        2. get_message()
        3. LinkExtractor.extract_confirmation_link()
        
        Args:
            timeout_seconds: 总超时秒数
            poll_interval: 轮询间隔秒数
            subject_patterns: 主题匹配正则列表
            sender_whitelist: 发件人白名单
        
        Returns:
            最终的确认链接 URL，如果超时或提取失败则返回 None
        """
        # 1. 等待验证邮件
        message = self.wait_for_verification_email(
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            subject_patterns=subject_patterns,
            sender_whitelist=sender_whitelist
        )
        
        if not message:
            return None
        
        # 2. 获取邮件详情
        try:
            detail = self.client.get_message(message.id)
        except Exception as e:
            log_message(f"获取邮件详情失败: {str(e)}")
            return None
        
        # 3. 提取确认链接
        link = LinkExtractor.extract_confirmation_link(detail)
        
        if link:
            log_message(f"成功提取确认链接: {link[:100]}...")
        else:
            log_message("提取确认链接失败")
        
        return link


# ============== 便捷函数 ==============
def create_duckmail_and_get_confirmation_link(
    email_prefix: Optional[str] = None,
    domain: str = DEFAULT_DOMAIN,
    password: Optional[str] = None,
    timeout_seconds: int = DEFAULT_POLL_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL
) -> tuple[Optional[DuckMailAccount], Optional[str]]:
    """
    一站式便捷函数：创建 DuckMail 账户并等待获取确认链接
    
    Args:
        email_prefix: 邮箱前缀，不传则自动生成
        domain: 邮箱域名，默认 duckmail.sbs
        password: DuckMail 账户密码，不传则自动生成
        timeout_seconds: 轮询超时秒数
        poll_interval: 轮询间隔秒数
    
    Returns:
        (DuckMailAccount, confirmation_link) 元组
        如果失败则相应字段为 None
    """
    client = DuckMailClient()
    
    try:
        # 1. 创建账户
        account = client.create_account(
            email_prefix=email_prefix,
            domain=domain,
            password=password
        )
        
        # 2. 获取 token
        client.get_token()
        
        # 3. 轮询等待确认链接
        poller = MailPoller(client)
        link = poller.get_confirmation_link(
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval
        )
        
        return account, link
        
    except Exception as e:
        log_message(f"一站式流程失败: {str(e)}")
        return client.account, None


# ============== 测试代码 ==============
if __name__ == "__main__":
    # 简单测试：创建账户并获取 token
    print("=" * 50)
    print("DuckMail 客户端测试")
    print("=" * 50)
    
    client = DuckMailClient()
    
    # 测试创建账户
    try:
        account = client.create_account()
        print(f"✓ 创建账户成功: {account.address}")
    except Exception as e:
        print(f"✗ 创建账户失败: {e}")
        exit(1)
    
    # 测试获取 token
    try:
        token = client.get_token()
        print(f"✓ 获取 token 成功: {token[:30]}...")
    except Exception as e:
        print(f"✗ 获取 token 失败: {e}")
        exit(1)
    
    # 测试获取邮件列表
    try:
        messages = client.list_messages()
        print(f"✓ 获取邮件列表成功: {len(messages)} 封")
    except Exception as e:
        print(f"✗ 获取邮件列表失败: {e}")
    
    print("=" * 50)
    print("测试完成")
    print(f"测试邮箱: {account.address}")
    print(f"测试密码: {account.password}")
    print("=" * 50)