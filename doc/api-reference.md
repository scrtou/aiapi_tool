# API 参考

本文档描述 `aiapi_tool` 各服务的 HTTP API。

> 说明：以下路径均是**各服务自身**暴露的路径，而不是单一网关下的全局唯一路径。
> 例如：`registration-service` 与 `orchestrator-service` 都存在 `/api/v1/events/{task_id}`，但它们属于不同服务。

所有服务默认前缀为 `/api/v1`。

---

## 1. 公共约定

### 1.1 认证方式

支持以下几种认证：

1. **项目 API Key**
   - 请求头：`Authorization: Bearer <api_key>`
   - 可选：`X-Project-Id: <project_id>`
2. **内部服务调用**
   - 请求头：`X-Internal-Token: <token>`
   - 可选：`X-Project-Id: <project_id>`
3. **管理员 API Key**
   - 使用 Bearer API Key，但具备 admin 权限
   - 可访问内部/管理类接口

### 1.2 Trace

所有服务都支持：

- `X-Trace-Id: <trace_id>`

如果不传，服务会自动生成。

### 1.3 通用响应包结构

成功响应：

```json
{
  "success": true,
  "trace_id": "trc_xxx",
  "data": {},
  "error": null
}
```

失败响应：

```json
{
  "success": false,
  "trace_id": "trc_xxx",
  "data": null,
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "resource not found",
    "service": "mail-service",
    "state": "get_account",
    "retryable": false,
    "details": {}
  }
}
```

### 1.4 健康检查接口

所有服务都有：

- `GET /api/v1/health`

部分服务还有：

- `GET /api/v1/health/details`
- `GET /api/v1/admin/metrics`

这些增强健康接口通常要求内部或管理员权限。

### 1.5 幂等说明

以下 workflow 创建/重试接口支持：

- `Idempotency-Key: <key>`

同一 `project_id` 下：

- 若 `Idempotency-Key` 与请求体完全相同，则返回已有任务
- 若 `Idempotency-Key` 相同但请求体不同，则返回 `409`

### 1.6 callback 说明

`workflow` 创建接口支持请求体内传入：

```json
{
  "callback": {
    "url": "https://example.com/webhooks/workflow",
    "headers": {
      "X-Signature": "demo"
    },
    "timeout_seconds": 15,
    "secret": "callback_secret",
    "max_attempts": 3,
    "retry_backoff_seconds": 3
  }
}
```

任务进入终态后，平台会向该地址发送 `POST` JSON 回调：

- callback 事件先落库到 SQLite outbox
- 同一任务终态对应稳定 `event_id`
- 若提供 `secret`，请求头会带：
  - `X-Callback-Signature-256: sha256=<hmac>`
  - `X-Callback-Event-Id: <event_id>`
- 回调失败后会按 `max_attempts` 与 `retry_backoff_seconds` 重试
- 服务重启后会恢复未送达 callback

---

## 2. mail-service

### 2.1 健康接口

#### `GET /api/v1/health`
- 鉴权：无
- 返回：服务基础健康状态

#### `GET /api/v1/health/details`
- 鉴权：内部或管理员
- 返回：mail-service 详细指标快照

#### `GET /api/v1/admin/metrics`
- 鉴权：内部或管理员
- 返回：mail-service 指标快照（与 `health/details` 同源）

### 2.2 邮箱账户接口

#### `GET /api/v1/mail/accounts`
- 鉴权：`mail:read`
- Query：
  - `provider?: string`
  - `status?: string`
  - `limit?: int = 50`
- 返回：`MailAccountsData`

返回 `data` 示例：

```json
{
  "accounts": [
    {
      "project_id": "demo",
      "provider": "smailpro_web",
      "account_id": "acct_xxx",
      "address": "demo@gmail.com",
      "password": "secret",
      "status": "active",
      "expires_at": null,
      "meta": {}
    }
  ],
  "total": 1
}
```

#### `POST /api/v1/mail/accounts`
- 鉴权：`mail:create`
- 请求体：`CreateMailAccountRequest`

请求示例：

```json
{
  "provider": "smailpro_web",
  "domain": null,
  "pattern": "random@gmail.com-1",
  "expiry_time_ms": 3600000,
  "options": {}
}
```

返回：`CreateMailAccountData`

#### `GET /api/v1/mail/accounts/{account_id}`
- 鉴权：`mail:read`
- 返回：

```json
{
  "account": {
    "project_id": "demo",
    "provider": "smailpro_web",
    "account_id": "acct_xxx",
    "address": "demo@gmail.com",
    "password": "secret",
    "status": "active",
    "expires_at": null,
    "meta": {}
  }
}
```

#### `DELETE /api/v1/mail/accounts/{account_id}`
- 鉴权：`mail:delete`
- 返回：`DeleteMailAccountData`

```json
{
  "deleted": true
}
```

### 2.3 邮件接口

#### `GET /api/v1/mail/accounts/{account_id}/messages`
- 鉴权：`mail:read`
- 返回：`MailMessagesData`

```json
{
  "messages": [
    {
      "id": "msg_xxx",
      "from_address": "noreply@example.com",
      "from_name": "Example",
      "subject": "Welcome",
      "received_at": "2026-03-14T07:00:00Z",
      "seen": false
    }
  ],
  "next_cursor": null,
  "total": 1
}
```

#### `GET /api/v1/mail/accounts/{account_id}/messages/{message_id}`
- 鉴权：`mail:read`
- 返回：`MailMessageData`

```json
{
  "message": {
    "id": "msg_xxx",
    "from_address": "noreply@example.com",
    "subject": "Welcome",
    "text": "plain text body",
    "html": "<html>...</html>",
    "attachments": []
  }
}
```

#### `POST /api/v1/mail/accounts/{account_id}/extract-confirmation-link`
- 鉴权：`mail:read`
- 请求体：`ExtractConfirmationLinkRequest`

```json
{
  "message_id": "msg_xxx",
  "ruleset": "generic"
}
```

- 返回：`ExtractConfirmationLinkData`

```json
{
  "confirmation_link": "https://example.com/verify?token=abc"
}
```

### 2.4 Provider 管理接口

> 以下接口均为管理接口，要求内部或管理员权限。

#### `GET /api/v1/mail/providers`
- 返回所有 mail provider 的启用状态、健康状态与能力信息

#### `GET /api/v1/mail/providers/{provider_name}/domains`
- 返回 provider 可用域名
- 对 `smailpro_web`，返回中除 `domains` 外还会包含：
  - `domain_groups`
  - `server_groups`
  - `page_status_code`
  - `page_url`
  - `browser_ready`
  - `headless`
  - `auto_visible_fallback`
  - `warning`（如适用）

#### `POST /api/v1/mail/providers/{provider_name}/health-check`
- 执行 provider 健康检查
- 对 `smailpro_web`，会额外返回页面解析、浏览器检查结果，以及 `headless -> visible` 自动回退开关状态

#### `POST /api/v1/mail/providers/{provider_name}/enable`
- 启用 provider

#### `POST /api/v1/mail/providers/{provider_name}/disable`
- 禁用 provider

---

## 3. proxy-service

### 3.1 健康接口

#### `GET /api/v1/health`
- 鉴权：无

#### `GET /api/v1/health/details`
- 鉴权：内部或管理员

#### `GET /api/v1/admin/metrics`
- 鉴权：内部或管理员

### 3.2 代理租约接口

#### `GET /api/v1/proxies`
- 鉴权：`proxy:read`
- Query：
  - `provider?: string`
  - `status?: string`
  - `limit?: int = 50`
- 返回：`ProxyLeasesData`

#### `POST /api/v1/proxies/lease`
- 鉴权：`proxy:lease`
- 请求体：`LeaseProxyRequest`

```json
{
  "scheme": ["http", "https"],
  "country": ["US"],
  "sticky": false,
  "ttl_seconds": 600,
  "tags": ["registration"]
}
```

- 返回：`LeaseProxyData`

#### `POST /api/v1/proxies/{proxy_id}/release`
- 鉴权：`proxy:lease`
- 返回：`ReleaseProxyData`

```json
{
  "released": true
}
```

### 3.3 代理池管理接口

> 以下接口均为内部或管理员接口。

路由前缀：`/api/v1/proxy-pools`

#### `GET /api/v1/proxy-pools`
- 返回代理池列表、条目与健康状态

#### `POST /api/v1/proxy-pools`
- 请求体：`dict`
- 当前未定义严格 schema，payload 会直接持久化为 pool 记录

建议字段示例：

```json
{
  "pool_id": "pool_demo",
  "name": "demo pool",
  "provider": "managed_pool",
  "status": "enabled",
  "tags": ["default"]
}
```

#### `DELETE /api/v1/proxy-pools/{pool_id}`
- 删除代理池

#### `POST /api/v1/proxy-pools/{pool_id}/enable`
- 启用代理池

#### `POST /api/v1/proxy-pools/{pool_id}/disable`
- 禁用代理池

#### `POST /api/v1/proxy-pools/{pool_id}/entries`
- 为代理池新增条目
- 请求体：`dict`

建议字段示例：

```json
{
  "provider": "custom",
  "scheme": "http",
  "host": "1.2.3.4",
  "port": 8080,
  "username": "user",
  "password": "pass",
  "country": "US",
  "status": "enabled",
  "tags": ["residential"]
}
```

#### `DELETE /api/v1/proxy-pools/entries/{proxy_entry_id}`
- 删除代理池条目

#### `POST /api/v1/proxy-pools/entries/{proxy_entry_id}/enable`
- 启用条目

#### `POST /api/v1/proxy-pools/entries/{proxy_entry_id}/disable`
- 禁用条目

#### `POST /api/v1/proxy-pools/entries/{proxy_entry_id}/health-check`
- 检查指定条目健康状态

---

## 4. registration-service

### 4.1 健康接口

#### `GET /api/v1/health`
- 鉴权：无

#### `GET /api/v1/health/details`
- 鉴权：内部或管理员

#### `GET /api/v1/admin/metrics`
- 鉴权：内部或管理员

### 4.2 注册任务接口

> 以下接口均要求内部或管理员权限。

#### `GET /api/v1/registrations/tasks`
- Query：
  - `status?: string`
  - `state?: string`
  - `site?: string`
  - `limit?: int = 50`
- 返回：`RegistrationTasksData`

#### `POST /api/v1/registrations/tasks`
- 请求体：`CreateRegistrationTaskRequest`

```json
{
  "site": "nexos",
  "identity": {
    "first_name": "John",
    "last_name": "Doe",
    "password": "Secret123!"
  },
  "mail_account": {
    "provider": "smailpro_web",
    "account_id": "acct_xxx",
    "address": "demo@gmail.com",
    "password": "secret",
    "status": "active",
    "expires_at": null,
    "meta": {}
  },
  "proxy": null,
  "strategy": {}
}
```

- 返回：`RegistrationTaskData`

#### `GET /api/v1/registrations/tasks/{task_id}`
- 返回：`RegistrationTaskDetailData`
- 包含：
  - `task`
  - `result`
  - `error`
  - `artifacts`
  - `events`

#### `POST /api/v1/registrations/tasks/{task_id}/cancel`
- 取消任务
- 返回：`RegistrationTaskData`

### 4.3 注册事件与 artifacts

> 以下接口均要求内部或管理员权限。

#### `GET /api/v1/events/{task_id}`
- 返回注册任务事件流

```json
{
  "events": [
    {
      "time": "2026-03-14T07:00:00Z",
      "service": "registration-service",
      "task_id": "tsk_xxx",
      "status": "running",
      "state": "verification_email",
      "level": "info",
      "message": "waiting for verification email",
      "data": {}
    }
  ]
}
```

#### `GET /api/v1/artifacts/{task_id}`
- 返回任务 artifacts 列表

#### `GET /api/v1/artifacts/{task_id}/{artifact_name}`
- 直接下载 artifact 文件
- 成功时返回文件流，不再包裹为 JSON envelope

---

## 5. login-service

### 5.1 健康接口

#### `GET /api/v1/health`
- 鉴权：无

### 5.2 登录接口

#### `POST /api/v1/logins`
- 鉴权：`login:run`
- 请求体：`LoginRequest`

```json
{
  "site": "nexos",
  "credentials": {
    "email": "user@example.com",
    "password": "Secret123!"
  },
  "proxy": null,
  "strategy": {}
}
```

- 返回：`LoginData`

#### `POST /api/v1/logins/verify-session`
- 鉴权：`login:verify`
- 请求体：`VerifySessionRequest`

```json
{
  "site": "nexos",
  "token": "session_or_access_token"
}
```

- 返回：`VerifySessionData`

```json
{
  "valid": true,
  "identity": {
    "external_subject": "sub_xxx",
    "external_user_id": "user_xxx"
  },
  "site_result": {}
}
```

#### `GET /api/v1/logins/results`
- 鉴权：内部或管理员
- Query：
  - `site?: string`
  - `limit?: int = 50`
- 返回：`LoginResultsData`

---

## 6. orchestrator-service

### 6.1 健康接口

#### `GET /api/v1/health`
- 鉴权：无

#### `GET /api/v1/health/details`
- 鉴权：内部或管理员

#### `GET /api/v1/admin/metrics`
- 鉴权：内部或管理员

### 6.2 Workflow 接口

#### `GET /api/v1/workflows`
- 鉴权：`workflow:read`
- Query：
  - `status?: string`
  - `state?: string`
  - `site?: string`
  - `limit?: int = 50`
- 返回：`WorkflowTasksData`

#### `POST /api/v1/workflows/register-and-login`
- 鉴权：`workflow:run`
- 支持请求头：`Idempotency-Key`
- 请求体：`RegisterWorkflowRequest`

#### `POST /api/v1/workflows/register`
- 鉴权：`workflow:run`
- 支持请求头：`Idempotency-Key`
- 请求体：`RegisterWorkflowRequest`

`RegisterWorkflowRequest` 示例：

```json
{
  "site": "nexos",
  "mail_policy": {
    "providers": ["smailpro_web", "gptmail"],
    "domain_preference": ["gmail.com"],
    "expiry_time_ms": 3600000
  },
  "proxy_policy": {
    "enabled": false,
    "lease_request": {}
  },
  "identity": {
    "first_name": "John",
    "last_name": "Doe",
    "password": "Secret123!"
  },
  "strategy": {
    "registration_mode": "api_first",
    "login_mode": "api_first",
    "timeout_seconds": 360
  },
  "callback": {
    "url": "https://example.com/webhooks/workflow",
    "headers": {},
    "timeout_seconds": 15,
    "secret": "callback_secret",
    "max_attempts": 3,
    "retry_backoff_seconds": 3
  }
}
```

#### `POST /api/v1/workflows/login`
- 鉴权：`workflow:run`
- 支持请求头：`Idempotency-Key`
- 请求体：`LoginWorkflowRequest`

```json
{
  "site": "nexos",
  "credentials": {
    "email": "user@example.com",
    "password": "Secret123!"
  },
  "proxy_policy": {
    "enabled": false,
    "lease_request": {}
  },
  "strategy": {
    "registration_mode": "api_first",
    "login_mode": "api_first",
    "timeout_seconds": 360
  },
  "callback": null
}
```

#### `POST /api/v1/workflows/{task_id}/retry`
- 鉴权：`workflow:run`
- 支持请求头：`Idempotency-Key`
- 返回：`WorkflowTaskData`

#### `POST /api/v1/workflows/{task_id}/cancel`
- 鉴权：`workflow:run`
- 返回：`WorkflowTaskData`

#### `GET /api/v1/workflows/{task_id}`
- 鉴权：`workflow:read`
- 返回：`WorkflowTaskDetailData`
- 包含：
  - `task`
  - `result`
  - `error`
  - `artifacts`
  - `events`

### 6.3 Workflow 事件与 artifacts

> 以下接口均要求内部或管理员权限。

#### `GET /api/v1/events/{task_id}`
- 返回 workflow 事件流

#### `GET /api/v1/artifacts/{task_id}`
- 返回 workflow artifacts 列表
- 若 workflow 自身无 artifact，可能回退聚合 registration-service 的 artifacts

#### `GET /api/v1/artifacts/{task_id}/{artifact_name}`
- 下载 artifact 文件
- 成功时返回文件流，不再包裹为 JSON envelope

### 6.4 代理与回调行为说明

- 当 `proxy_policy.enabled=true` 时，编排层会自动调用 `proxy-service` 申请并释放代理
- 取消 workflow 时，若已创建下游注册任务，编排层会联动请求 `registration-service` 取消
- workflow 进入终态后，如请求中包含 callback 配置，则会异步触发 callback 投递

---

## 7. 常用模型速查

### 7.1 MailAccount

```json
{
  "project_id": "demo",
  "provider": "smailpro_web",
  "account_id": "acct_xxx",
  "address": "demo@gmail.com",
  "password": "secret",
  "status": "active",
  "expires_at": null,
  "meta": {}
}
```

### 7.2 ProxyLease

```json
{
  "project_id": "demo",
  "proxy_id": "proxy_xxx",
  "provider": "managed_pool",
  "scheme": "http",
  "host": "1.2.3.4",
  "port": 8080,
  "username": "user",
  "password": "pass",
  "country": "US",
  "expires_at": null,
  "meta": {}
}
```

### 7.3 TaskEvent

```json
{
  "time": "2026-03-14T07:00:00Z",
  "service": "registration-service",
  "task_id": "tsk_xxx",
  "status": "running",
  "state": "verification_email",
  "level": "info",
  "message": "waiting for verification email",
  "data": {}
}
```

---

## 8. 备注

1. 本文档以当前代码实现为准。
2. 某些内部管理接口（尤其 `proxy-pools`）当前仍使用宽松 `dict` payload，而非严格 Pydantic schema。
3. 若后续新增站点适配器、provider 能力或 workflow 字段，应同步更新本文件。
