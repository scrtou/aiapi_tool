# 配置说明

## 全局
- `APP_DB_PATH`
- `ARTIFACTS_STORAGE_PATH`
- `INTERNAL_SERVICE_TOKEN`
- `INTERNAL_SERVICE_PROJECT_ID`
- `PLATFORM_API_KEYS_JSON`

### `PLATFORM_API_KEYS_JSON` 格式

用于公开 API 的 Bearer API Key 配置，支持 JSON 数组：

```json
[
  {
    "key": "demo_public_key",
    "project_id": "demo-project",
    "scopes": [
      "workflow:run",
      "workflow:read",
      "login:run",
      "login:verify",
      "mail:create",
      "mail:read",
      "mail:delete",
      "proxy:lease",
      "proxy:read"
    ]
  }
]
```

可选字段：

- `name`
- `enabled`
- `is_admin`

`is_admin=true` 的 key 可以跨项目查看受限接口，并可配合 `X-Project-Id` 指定目标项目。

## GPTMail
- `GPTMAIL_BASE_URL`
- `GPTMAIL_API_KEY`

## MoeMail
- `MOEMAIL_BASE_URL`
- `MOEMAIL_API_KEY`
- `MOEMAIL_EXPIRY_TIME`

## DuckMail
- `DUCKMAIL_BASE_URL`
- `DUCKMAIL_DOMAIN`
- `DUCKMAIL_CREATE_MAX_ATTEMPTS`

## Chayns
- `CHAYNS_AUTH_API_BASE_URL`
- `CHAYNS_AUTH_REGISTER_API_BASE_URL`
- `CHAYNS_AUTH_CHECK_ALIAS_SITE_ID`
- `CHAYNS_LOCATION_ID`
- `CHAYNS_LOGIN_TOKEN_TYPE`
- `MCAPTCHA_BASE_URL`

## Nexos
- `NEXOS_BASE_URL`
- `NEXOS_ORY_BASE_URL`
- `NEXOS_TURNSTILE_SITE_KEY`
- `NEXOS_TURNSTILE_PAGE_URL`
- `NEXOS_HTTP_TIMEOUT_SECONDS`
- `NEXOS_TURNSTILE_TIMEOUT_SECONDS`
- `NEXOS_TURNSTILE_POLL_INTERVAL_SECONDS`
- `NEXOS_MAIL_WAIT_SECONDS`
- `NEXOS_MAIL_POLL_INTERVAL_SECONDS`
- `NEXOS_CAPTCHA_PROVIDER`
- `NEXOS_2CAPTCHA_API_KEY`
- `NEXOS_CAPSOLVER_API_KEY`
- `NEXOS_ENABLE_BROWSER_TURNSTILE_FALLBACK`
- `NEXOS_BROWSER_TURNSTILE_HEADLESS`
- `NEXOS_BROWSER_TURNSTILE_WAIT_SECONDS`
- `NEXOS_BROWSER_OS`
- `NEXOS_BROWSER_LOCALE`
- `NEXOS_BROWSER_WINDOW_WIDTH`
- `NEXOS_BROWSER_WINDOW_HEIGHT`
- `NEXOS_BROWSER_HUMANIZE`
- `NEXOS_BROWSER_PROXY_URL`
- `NEXOS_BROWSER_CLICK_ATTEMPTS`
- `NEXOS_BROWSER_POST_SUBMIT_WAIT_SECONDS`
- `NEXOS_DRISSION_BROWSER_PATH`
- `NEXOS_DRISSION_HEADLESS`
- `NEXOS_DRISSION_WINDOW_WIDTH`
- `NEXOS_DRISSION_WINDOW_HEIGHT`
- `NEXOS_DRISSION_PROXY_URL`
- `NEXOS_DRISSION_MAIL_WAIT_SECONDS`
- `NEXOS_DRISSION_MAIL_POLL_INTERVAL_SECONDS`
- `NEXOS_DRISSION_TURNSTILE_TIMEOUT_SECONDS`
- `NEXOS_DRISSION_LOGIN_WAIT_SECONDS`
- `NEXOS_DRISSION_DEBUG_DIR`

说明：
- `nexos` 的注册流程依赖 Cloudflare Turnstile。
- 可直接传入 `strategy.captcha.turnstile_token`，或配置 `2captcha/capsolver` 自动求解。
- 若未配置 solver，可开启浏览器回退；当前实现会优先走完整浏览器注册流程，而不是只在首页提取 token。
- 浏览器回退默认使用 `Camoufox`，可配合 `ProxyLease` 或 `NEXOS_BROWSER_PROXY_URL` 改变出口 IP。
- 若 `strategy.registration_mode=drission` 或 `strategy.login_mode=drission`，将使用 `DrissionPage` 执行稳定的完整浏览器注册/登录流程。

## 服务间调用
- `MAIL_SERVICE_URL`
- `PROXY_SERVICE_URL`
- `REGISTRATION_SERVICE_URL`
- `LOGIN_SERVICE_URL`
- `WORKFLOW_HTTP_TIMEOUT_SECONDS`
- `WORKFLOW_TASK_POLL_INTERVAL_SECONDS`
- `WORKFLOW_TASK_MAX_POLLS`
- `REGISTRATION_WORKER_POLL_INTERVAL_SECONDS`
- `WORKFLOW_WORKER_POLL_INTERVAL_SECONDS`
- `REGISTRATION_ENABLE_STARTUP_RECOVERY`
- `REGISTRATION_ENABLE_EMBEDDED_WORKER`
- `WORKFLOW_ENABLE_STARTUP_RECOVERY`
- `WORKFLOW_ENABLE_EMBEDDED_WORKER`

## 认证与项目上下文

- 对外公开接口：`Authorization: Bearer <api_key>`
- 服务间调用：`X-Internal-Token: <token>`
- 项目透传：`X-Project-Id: <project_id>`

说明：

- 普通项目 API Key 只能访问自身 `project_id` 下的数据
- 内部调用和管理员 API Key 可以跨项目访问
- `health` 接口默认无需鉴权

## 浏览器
- `CHROME_BINARY`
- `CHROMEDRIVER_PATH`
- `SMAILPRO_WEB_HEADLESS`
- `SMAILPRO_WEB_PROFILE_DIR`


## CORS
- `CORS_ALLOW_ORIGINS`
- `CORS_ALLOW_METHODS`
- `CORS_ALLOW_HEADERS`
- `CORS_EXPOSE_HEADERS`
- `CORS_ALLOW_CREDENTIALS`

说明：
- 前端跨域访问 `aiapi_tool` 时，浏览器会先发 `OPTIONS` 预检请求。
- 若未开启 CORS，中间层会返回 `405 Method Not Allowed`。
- 开发阶段可用 `CORS_ALLOW_ORIGINS=*`；生产建议改成具体前端地址，例如 `http://23.19.231.152:5560`.
