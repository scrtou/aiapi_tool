# 运维与运行说明

## 启动全部服务

```bash
docker compose up --build
```

## 默认端口
- `orchestrator-service`: `8000`
- `mail-service`: `8001`
- `proxy-service`: `8002`
- `registration-service`: `8003`
- `login-service`: `8004`

## 健康检查

每个服务均提供：
```text
GET /api/v1/health
```

## 推荐主链路

```text
gptmail -> chayns register API -> confirmation email -> register/verify -> login API
```

## 常见排查点
- 邮箱 provider 是否可创建账号
- `checkalias` 是否返回 `204`
- 注册任务是否卡在 `waiting_email`
- login-service 是否返回 `type=1` token
- artifacts 与 events 是否已落库


## 任务恢复

- 服务重启后，未完成的 `workflow/registration` 任务会在 startup 阶段被自动收敛。
- 已请求取消的任务会被标记为 `cancelled`。
- 其它非终态任务会被标记为 `failed/service_restarted`，客户端可走 retry。


## Worker 说明

- `registration-service` 和 `orchestrator-service` 在 startup 时会启动本地 worker 循环。
- 新创建的 queued 任务由 worker 从 SQLite 中拉取执行。
- `REGISTRATION_WORKER_POLL_INTERVAL_SECONDS` 与 `WORKFLOW_WORKER_POLL_INTERVAL_SECONDS` 控制轮询间隔。


## 独立 worker 进程

- `docker-compose.yml` 已拆出 `registration-worker` 与 `orchestrator-worker`。
- Web 服务默认通过 `REGISTRATION_ENABLE_EMBEDDED_WORKER=0`、`WORKFLOW_ENABLE_EMBEDDED_WORKER=0` 关闭内嵌 worker。
- 独立 worker 进程负责 startup recovery 和 queued 任务消费。


## Admin Metrics

- `registration-service` 与 `orchestrator-service` 提供 `GET /api/v1/health/details` 与 `GET /api/v1/admin/metrics`。
- 指标包含队列深度、任务状态分布、worker 心跳、当前活跃任务等。


## 多 Worker 消费

- `workflow` 与 `registration` 的 worker 通过 SQLite 原子 claim 领取 queued 任务。
- 多个 worker 进程并发时，同一任务只会被一个 worker 成功领取。
- 指标接口会返回 `workers` 列表，展示每个 worker 的 heartbeat。


## Callback Outbox

- `orchestrator-service` 会把 callback 事件先落库到 SQLite outbox。
- 同一任务终态对应稳定 `event_id`，重复恢复/重试不会生成新的 callback event。
- startup 时会自动恢复未送达的 callback event。


## CORS 排查

- 如果浏览器报跨域失败，并且服务日志出现大量 `OPTIONS ... 405 Method Not Allowed`，说明是预检请求未通过。
- 请检查 `CORS_ALLOW_ORIGINS` 是否包含前端页面来源。
- 若前端运行在 `http://23.19.231.152:5560`，则后端至少应允许该 origin。
