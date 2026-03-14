from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from libs.clients.nexos_client import NEXOS_BASE_URL
from libs.contracts.mail import MailAccount
from libs.contracts.proxy import ProxyLease
from libs.contracts.registration import RegistrationIdentity
from libs.core.config import env_bool, env_int, env_str
from libs.core.exceptions import ServiceError


if TYPE_CHECKING:
    from services.registration_service.adapters.nexos import NexosRegistrationAdapter


NEXOS_BROWSER_OS = env_str("NEXOS_BROWSER_OS", "windows") or "windows"
NEXOS_BROWSER_LOCALE = env_str("NEXOS_BROWSER_LOCALE", "zh-CN") or "zh-CN"
NEXOS_BROWSER_WINDOW_WIDTH = env_int("NEXOS_BROWSER_WINDOW_WIDTH", 1280)
NEXOS_BROWSER_WINDOW_HEIGHT = env_int("NEXOS_BROWSER_WINDOW_HEIGHT", 720)
NEXOS_BROWSER_HUMANIZE = env_bool("NEXOS_BROWSER_HUMANIZE", False)
NEXOS_BROWSER_PROXY_URL = env_str("NEXOS_BROWSER_PROXY_URL")
NEXOS_BROWSER_CLICK_ATTEMPTS = env_int("NEXOS_BROWSER_CLICK_ATTEMPTS", 7)
NEXOS_BROWSER_POST_SUBMIT_WAIT_SECONDS = env_int("NEXOS_BROWSER_POST_SUBMIT_WAIT_SECONDS", 6)


class NexosBrowserFlow:
    def __init__(self, adapter: "NexosRegistrationAdapter", *, proxy: ProxyLease | None = None):
        self.adapter = adapter
        self.proxy = proxy

    def _log(self, logs: list[str], message: str):
        self.adapter._log(logs, message)

    def _cancel_check(self, strategy: dict | None):
        self.adapter._cancel_check(strategy)

    def _captcha_config(self, strategy: dict | None) -> dict[str, Any]:
        return self.adapter._captcha_config(strategy)

    def _proxy_url(self) -> str | None:
        if NEXOS_BROWSER_PROXY_URL:
            return NEXOS_BROWSER_PROXY_URL
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

    def _proxy_config(self) -> dict[str, str] | None:
        proxy_url = self._proxy_url()
        if not proxy_url:
            return None
        return {"server": proxy_url}

    def _dismiss_cookie_banner(self, page, logs: list[str]):
        for selector in [
            "button:has-text('OK, understood')",
            "button:has-text('Accept')",
            "button:has-text('I agree')",
        ]:
            try:
                page.locator(selector).click(timeout=4000)
                self._log(logs, f"浏览器已关闭 Cookie 弹窗: {selector}")
                time.sleep(0.5)
                return
            except Exception:
                continue

    def _click_first(self, page, selectors: list[str], logs: list[str], timeout_ms: int = 10000, force: bool = False) -> bool:
        for selector in selectors:
            try:
                page.locator(selector).first.click(timeout=timeout_ms, force=force)
                self._log(logs, f"浏览器点击成功: {selector}")
                return True
            except Exception:
                continue
        return False

    def _fill_first(self, page, selectors: list[str], value: str, logs: list[str], timeout_ms: int = 10000) -> bool:
        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(timeout=timeout_ms)
                page.locator(selector).first.fill(value, timeout=timeout_ms)
                self._log(logs, f"浏览器填充成功: {selector}")
                return True
            except Exception:
                continue
        return False

    def _password_submit_state(self, page) -> dict[str, Any]:
        return page.evaluate(
            """
            () => {
              const tokenInput = document.querySelector('input[name="cf-turnstile-response"]');
              const overlay = document.querySelector('.fixed.inset-0.z-20');
              const submit = document.querySelector('button[name="method"][value="password"]');
              return {
                token_length: tokenInput && tokenInput.value ? tokenInput.value.length : 0,
                submit_disabled: !!(submit && submit.disabled),
                overlay_opacity: overlay ? getComputedStyle(overlay).opacity : null,
                overlay_pointer_events: overlay ? getComputedStyle(overlay).pointerEvents : null,
                viewport: {
                  width: window.innerWidth,
                  height: window.innerHeight,
                },
              };
            }
            """
        )

    def _turnstile_is_solved(self, page) -> bool:
        state = self._password_submit_state(page)
        return bool(state.get("token_length") or not state.get("submit_disabled"))

    def _human_like_mouse_move(self, page, target_x: float, target_y: float, duration: float = 0.6):
        start_x = random.uniform(100, 500)
        start_y = random.uniform(100, 400)
        control_x1 = start_x + random.uniform(-100, 100)
        control_y1 = start_y + random.uniform(-100, 100)
        control_x2 = target_x + random.uniform(-50, 50)
        control_y2 = target_y + random.uniform(-50, 50)
        steps = max(20, int(duration * 80))
        for index in range(steps + 1):
            t = index / steps
            x = (1 - t) ** 3 * start_x + 3 * (1 - t) ** 2 * t * control_x1 + 3 * (1 - t) * t**2 * control_x2 + t**3 * target_x
            y = (1 - t) ** 3 * start_y + 3 * (1 - t) ** 2 * t * control_y1 + 3 * (1 - t) * t**2 * control_y2 + t**3 * target_y
            x += random.uniform(-1, 1)
            y += random.uniform(-1, 1)
            page.mouse.move(x, y)
            time.sleep(duration / steps)

    def _find_checkbox_by_edges(self, screenshot_path: Path) -> tuple[int, int] | None:
        try:
            import cv2
        except Exception:
            return None
        image = cv2.imread(str(screenshot_path))
        if image is None:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        image_height, image_width = image.shape[:2]
        candidates: list[dict[str, Any]] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            aspect_ratio = width / height if height else 0
            if 15 <= width <= 30 and 15 <= height <= 30 and 0.8 <= aspect_ratio <= 1.2:
                center_x = x + width // 2
                center_y = y + height // 2
                x_ratio = center_x / image_width
                y_ratio = center_y / image_height
                if 0.10 < x_ratio < 0.25 and 0.35 < y_ratio < 0.65:
                    score = (
                        abs(width * height - 400) * 0.5
                        + abs(aspect_ratio - 1.0) * 200
                        + abs(x_ratio - 0.16) * 500
                        + abs(y_ratio - 0.59) * 300
                    )
                    candidates.append({"x": center_x, "y": center_y, "score": score})
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"])
        best = candidates[0]
        return int(best["x"]), int(best["y"])

    def _click_turnstile(self, page, logs: list[str], strategy: dict | None, *, max_attempts: int = NEXOS_BROWSER_CLICK_ATTEMPTS) -> bool:
        screenshot_dir = Path(tempfile.mkdtemp(prefix="nexos-turnstile-"))
        time.sleep(2)
        for attempt in range(1, max_attempts + 1):
            self._cancel_check(strategy)
            if self._turnstile_is_solved(page):
                self._log(logs, f"Turnstile 已放行: attempt={attempt}")
                return True

            frame_locator = page.locator('iframe[src*="cloudflare"], iframe[title*="Widget"], iframe[id*="cf-"], iframe[name*="cf-"]')
            try:
                frame_count = frame_locator.count()
            except Exception:
                frame_count = 0

            click_target: tuple[int, int] | None = None
            if frame_count > 0:
                try:
                    iframe_box = frame_locator.first.bounding_box(timeout=3000)
                    if iframe_box:
                        click_target = (int(iframe_box["x"] + 30), int(iframe_box["y"] + 32))
                        self._log(logs, f"Turnstile iframe 点击定位成功: x={click_target[0]}, y={click_target[1]}")
                except Exception:
                    click_target = None

            if not click_target:
                screenshot_path = screenshot_dir / f"attempt_{attempt}.png"
                try:
                    page.screenshot(path=str(screenshot_path))
                    click_target = self._find_checkbox_by_edges(screenshot_path)
                    if click_target:
                        self._log(logs, f"Turnstile 边缘检测定位成功: x={click_target[0]}, y={click_target[1]}")
                except Exception:
                    click_target = None

            if not click_target:
                state = self._password_submit_state(page)
                viewport = state.get("viewport") or {"width": NEXOS_BROWSER_WINDOW_WIDTH, "height": NEXOS_BROWSER_WINDOW_HEIGHT}
                click_target = (int(viewport["width"] * 0.16), int(viewport["height"] * 0.59))
                self._log(logs, f"Turnstile 使用默认坐标点击: x={click_target[0]}, y={click_target[1]}")

            self._human_like_mouse_move(page, click_target[0], click_target[1], duration=random.uniform(0.5, 1.0))
            time.sleep(random.uniform(0.2, 0.5))
            page.mouse.click(click_target[0], click_target[1])
            self._log(logs, f"Turnstile 点击已执行: attempt={attempt}")
            time.sleep(3)

        solved = self._turnstile_is_solved(page)
        if solved:
            self._log(logs, "Turnstile 在最后一次检查时已放行")
        return solved

    def create_account(self, identity: RegistrationIdentity, mail_account: MailAccount, strategy: dict | None, logs: list[str]) -> dict[str, Any]:
        try:
            from camoufox.sync_api import Camoufox
        except Exception as exc:
            raise ServiceError(
                code="CAPTCHA_BROWSER_UNAVAILABLE",
                message=f"camoufox browser fallback is unavailable: {exc}",
                service="registration-service",
                state="captcha_browser",
                retryable=False,
                status_code=422,
            ) from exc

        config = self._captcha_config(strategy)
        page_url = str(config.get("page_url") or f"{NEXOS_BASE_URL.rstrip('/')}/authorization/login")
        headless = bool(config.get("browser_headless", env_bool("NEXOS_BROWSER_TURNSTILE_HEADLESS", True)))
        proxy_config = self._proxy_config()
        self._log(logs, f"使用浏览器完整流程创建账号: email={mail_account.address}, headless={headless}, proxy={'yes' if proxy_config else 'no'}")

        with Camoufox(
            headless=headless,
            os=NEXOS_BROWSER_OS,
            geoip=False,
            window=(NEXOS_BROWSER_WINDOW_WIDTH, NEXOS_BROWSER_WINDOW_HEIGHT),
            humanize=NEXOS_BROWSER_HUMANIZE,
            locale=NEXOS_BROWSER_LOCALE,
            proxy=proxy_config,
        ) as browser:
            page = browser.new_page()
            try:
                page.goto(page_url, timeout=60000)
                time.sleep(3)
                self._dismiss_cookie_banner(page, logs)

                if "/login" in page.url:
                    self._click_first(
                        page,
                        [
                            "[data-testid='login-page-sign-up-link']",
                            "a[href*='/authorization/registration']",
                        ],
                        logs,
                        timeout_ms=15000,
                        force=True,
                    )
                    time.sleep(2)
                    self._dismiss_cookie_banner(page, logs)

                if not self._fill_first(page, ["input[name='traits.email']", "[data-testid='auth-input-traits-email']"], mail_account.address, logs, timeout_ms=20000):
                    raise ServiceError(
                        code="REGISTRATION_BROWSER_FLOW_FAILED",
                        message="browser registration flow could not find email input",
                        service="registration-service",
                        state="registration_browser_email",
                        retryable=False,
                        status_code=422,
                    )

                self._click_first(page, ["input[name='traits.email']", "[data-testid='auth-input-traits-email']"], logs, timeout_ms=5000, force=True)

                if not self._click_first(page, ["button[name='method'][value='profile']", "button[name='method']", "[data-testid='auth-submit-method']"], logs, timeout_ms=20000, force=True):
                    raise ServiceError(
                        code="REGISTRATION_BROWSER_FLOW_FAILED",
                        message="browser registration flow could not submit email step",
                        service="registration-service",
                        state="registration_browser_profile",
                        retryable=False,
                        status_code=422,
                    )

                try:
                    page.locator("input[name='password']").wait_for(timeout=15000)
                except Exception:
                    self._log(logs, f"浏览器未进入密码页，准备重试邮箱提交: url={page.url}")
                    self._dismiss_cookie_banner(page, logs)
                    self._click_first(page, ["button[name='method'][value='profile']", "button[name='method']", "[data-testid='auth-submit-method']"], logs, timeout_ms=10000, force=True)
                    page.locator("input[name='password']").wait_for(timeout=15000)

                self._log(logs, "浏览器已进入密码页")
                self._fill_first(page, ["input[name='password']", "[data-testid='auth-password-password']"], identity.password, logs, timeout_ms=20000)

                solved = self._click_turnstile(page, logs, strategy)
                if not solved:
                    raise ServiceError(
                        code="CAPTCHA_SOLVER_FAILED",
                        message="browser fallback could not solve nexos turnstile challenge",
                        service="registration-service",
                        state="captcha_browser",
                        retryable=False,
                        status_code=422,
                    )

                if not self._click_first(page, ["button[name='method'][value='password']", "button[name='method']", "[data-testid='auth-submit-method']"], logs, timeout_ms=15000, force=True):
                    raise ServiceError(
                        code="REGISTRATION_BROWSER_FLOW_FAILED",
                        message="browser registration flow could not submit password step",
                        service="registration-service",
                        state="registration_browser_password",
                        retryable=False,
                        status_code=422,
                    )

                time.sleep(NEXOS_BROWSER_POST_SUBMIT_WAIT_SECONDS)
                page_text = page.evaluate("() => (document.body.innerText || '').slice(0, 1200)")
                cookies = page.context.cookies()
                self._log(logs, f"浏览器密码提交完成，当前 URL={page.url}")
                return {
                    "url": page.url,
                    "page_text": page_text,
                    "cookie_count": len(cookies),
                }
            except ServiceError:
                raise
            except Exception as exc:
                current_url = ""
                page_text = ""
                try:
                    current_url = page.url
                    page_text = page.evaluate("() => (document.body.innerText || '').slice(0, 1200)")
                except Exception:
                    pass
                raise ServiceError(
                    code="REGISTRATION_BROWSER_FLOW_FAILED",
                    message=str(exc),
                    service="registration-service",
                    state="registration_browser_flow",
                    retryable=False,
                    details={"url": current_url, "page_text": page_text[:500]},
                    status_code=422,
                ) from exc
