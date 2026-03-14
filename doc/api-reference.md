# API 参考

所有服务默认前缀为 `/api/v1`。

## 鉴权说明

- `GET /api/v1/health` 默认无需鉴权
- 公开接口默认需要 `Authorization: Bearer <api_key>`
- 内部接口默认需要 `X-Internal-Token`
- 部分高权限接口允许管理员 API Key 访问

## 幂等说明

以下创建型 workflow 接口支持：

- `Idempotency-Key: <key>`

同一 `project_id` 下，若 `Idempotency-Key` 和请求体完全相同，则返回已有任务；
若 `Idempotency-Key` 相同但请求体不同，则返回 `409`。

## callback 说明

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

任务进入终态后，平台会向该地址发送 `POST` JSON 回调。回调事件会先落库到 callback outbox。`event_id` 在同一任务终态下保持稳定，重复投递会复用同一个 `event_id`。若提供 `secret`，请求头会带 `X-Callback-Signature-256: sha256=<hmac>`，并同时带 `X-Callback-Event-Id`。回调失败时会按 `max_attempts` 和 `retry_backoff_seconds` 重试；服务重启后会继续恢复未送达的 callback event。

## mail-service

### 健康检查
- `GET /api/v1/health`

### 详细健康状态
- `GET /api/v1/health/details`

### Admin Metrics
- `GET /api/v1/admin/metrics`

### 详细健康状态
- `GET /api/v1/health/details`

### Admin Metrics
- `GET /api/v1/admin/metrics`

### 创建邮箱
- `POST /api/v1/mail/accounts`

请求示例：
```json
{
  "provider": "gptmail",
  "domain": null,
  "pattern": null,
  "expiry_time_ms": 3600000,
  "options": {}
}
```

### 查询邮箱
- `GET /api/v1/mail/accounts/{account_id}`

### 列出邮箱
- `GET /api/v1/mail/accounts`
- 支持 query：`provider` `status` `limit`

### 列出邮件
- `GET /api/v1/mail/accounts/{account_id}/messages`

### 获取单封邮件
- `GET /api/v1/mail/accounts/{account_id}/messages/{message_id}`

### 提取确认链接
- `POST /api/v1/mail/accounts/{account_id}/extract-confirmation-link`

## proxy-service

### 健康检查
- `GET /api/v1/health`

### 列出租约
- `GET /api/v1/proxies`

### 申请租约
- `POST /api/v1/proxies/lease`

### 释放租约
- `POST /api/v1/proxies/{proxy_id}/release`

## registration-service

### 健康检查
- `GET /api/v1/health`

### 创建注册任务
- `POST /api/v1/registrations/tasks`

### 查询注册任务
- `GET /api/v1/registrations/tasks/{task_id}`

### 取消注册任务
- `POST /api/v1/registrations/tasks/{task_id}/cancel`

### 列出注册任务
- `GET /api/v1/registrations/tasks`
- 支持 query：`status` `state` `site` `limit`

### 查询注册事件流
- `GET /api/v1/events/{task_id}`

### 查询注册 artifacts
- `GET /api/v1/artifacts/{task_id}`
- `GET /api/v1/artifacts/{task_id}/{artifact_name}`

## login-service

### 健康检查
- `GET /api/v1/health`

### 登录
- `POST /api/v1/logins`

### 验证 session
- `POST /api/v1/logins/verify-session`

### 列出登录结果
- `GET /api/v1/logins/results`
- 支持 query：`site` `limit`

## orchestrator-service

### 健康检查
- `GET /api/v1/health`

### 一键注册并登录
- `POST /api/v1/workflows/register-and-login`

### 仅注册
- `POST /api/v1/workflows/register`

### 仅登录
- `POST /api/v1/workflows/login`

### 重试 workflow
- `POST /api/v1/workflows/{task_id}/retry`

### 取消 workflow
- `POST /api/v1/workflows/{task_id}/cancel`

### 代理策略
- `workflow` 请求中的 `proxy_policy.enabled=true` 时，编排层会自动调用 `proxy-service` 申请并释放代理
- 取消 `workflow` 时，若已创建下游注册任务，编排层会联动请求 `registration-service` 取消

### 查询 workflow
- `GET /api/v1/workflows/{task_id}`

### 列出 workflows
- `GET /api/v1/workflows`
- 支持 query：`status` `state` `site` `limit`

### 查询 workflow 事件流
- `GET /api/v1/events/{task_id}`

### 查询 workflow artifacts
- `GET /api/v1/artifacts/{task_id}`
- `GET /api/v1/artifacts/{task_id}/{artifact_name}`
