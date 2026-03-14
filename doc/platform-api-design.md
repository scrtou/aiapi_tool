# 对外平台 API 设计方案

## 目标定位

`aiapi_tool` 后续可定位为“账号自动化能力平台”，而不只是当前项目内部使用的工具集。

目标是让外部项目通过统一 API 调用以下能力：

- 自动注册
- 自动登录
- 注册后登录
- 邮箱能力
- 代理能力

同时保留内部服务拆分，避免将浏览器自动化、邮件轮询、验证码处理等底层细节直接暴露给调用方。

## 对外开放原则

### 1. 优先暴露能力，而不是内部实现

外部项目真正需要的是：

- “帮我注册一个账号”
- “帮我登录一个账号”
- “帮我拿到 token / session”

而不是：

- 启动哪个 adapter
- Selenium 具体如何驱动
- 邮件轮询细节
- mCaptcha 处理细节

因此建议：

- 对外主入口以 `orchestrator-service` 为主
- 对外补充入口以 `login-service` 为辅
- `mail-service`、`proxy-service` 作为共享基础能力开放
- `workflow` 在启用 `proxy_policy` 时应自动编排代理申请与释放
- `registration-service` 保持“有限开放”，不作为最优先的外部集成入口

### 2. 对外开放不等于所有接口公开

每个服务应分成三类接口：

- 公开业务接口
  - 面向外部项目使用
- 受限运维接口
  - 面向管理员、内部工具或高权限项目
- 内部编排接口
  - 仅供服务间调用

### 3. 外部项目要看“项目隔离”，不是只看“是否鉴权”

如果后续多个项目共用同一个 `aiapi_tool`，仅有 token 鉴权还不够，必须有：

- `project_id`
- 配额
- 权限范围
- 审计日志

## 服务分层建议

### A. 对外主入口

#### `orchestrator-service`

建议作为统一业务网关，对外暴露：

- 创建注册并登录任务
- 查询任务状态
- 查询任务结果
- 查询失败原因
- 接收 webhook 回调

适合外部项目使用的原因：

- 屏蔽底层服务细节
- 便于后续替换内部实现
- 可统一做鉴权、限流、审计、幂等控制

### B. 对外能力服务

#### `login-service`

适合作为独立对外能力开放：

- 单独登录
- token/session 校验
- 后续扩展多站点登录能力

#### `mail-service`

适合作为共享基础服务开放：

- 创建邮箱
- 查询邮件
- 提取确认链接

但建议分层：

- 基础邮箱能力可开放
- 全量账号枚举、删除、内部调试字段应受限

#### `proxy-service`

适合作为共享基础服务开放：

- 申请代理
- 释放代理
- 查询当前项目自己的租约

但不建议直接开放：

- 所有项目的全量代理租约列表
- 原始供应商配置

### C. 内部能力服务

#### `registration-service`

建议保持“有限开放”：

- 可以保留外部调用能力
- 但优先推荐外部项目调 `orchestrator-service`

原因：

- 注册流程比登录更重
- 浏览器自动化更脆弱
- 内部实现未来更可能频繁调整

## 推荐的公开 API 面

## 第一阶段公开 API

### `orchestrator-service`

- `POST /api/v1/workflows/register-and-login`
- `POST /api/v1/workflows/register`
- `POST /api/v1/workflows/login`
- `GET /api/v1/workflows/{task_id}`
- `GET /api/v1/workflows`

建议后续新增：

- `POST /api/v1/workflows/{task_id}/cancel`

当前已支持：

- `POST /api/v1/workflows/{task_id}/retry`
- `POST /api/v1/workflows/{task_id}/cancel`
- `POST /api/v1/registrations/tasks/{task_id}/cancel`（内部/高权限）

### `login-service`

- `POST /api/v1/logins`
- `POST /api/v1/logins/verify-session`

建议后续补充：

- `GET /api/v1/logins/results/{result_id}`

### `mail-service`

- `POST /api/v1/mail/accounts`
- `GET /api/v1/mail/accounts/{account_id}`
- `GET /api/v1/mail/accounts/{account_id}/messages`
- `GET /api/v1/mail/accounts/{account_id}/messages/{message_id}`
- `POST /api/v1/mail/accounts/{account_id}/extract-confirmation-link`

### `proxy-service`

- `POST /api/v1/proxies/lease`
- `POST /api/v1/proxies/{proxy_id}/release`
- `GET /api/v1/proxies`

## 第二阶段公开 API

建议等平台能力补齐后再开放：

- `registration-service` 任务接口
- `events` 查询接口
- `artifacts` 下载接口

这些接口更适合：

- 内部排障
- 管理后台
- 高权限客户

不适合一开始就作为通用外部入口。

## 鉴权模型建议

## 两层鉴权

建议保留两套认证：

### 1. 外部项目鉴权

用于外部业务方调用公开 API。

推荐模型：

- `Authorization: Bearer <project_api_key>`

服务端解析后得到：

- `project_id`
- `environment`
- `scopes`
- `rate_limit_plan`

### 2. 内部服务鉴权

用于服务间调用。

推荐模型：

- 继续保留 `X-Internal-Token`
- 后续可升级为 mTLS 或 service identity

注意：

- 外部 token 不应直接拥有内部高权限
- 内部 token 只用于内部路由或特权操作

## 权限范围建议

建议 API Key 支持 scope：

- `workflow:run`
- `workflow:read`
- `login:run`
- `mail:create`
- `mail:read`
- `proxy:lease`
- `proxy:read`
- `admin:debug`

这样后续可以支持：

- 某项目只能登录，不能注册
- 某项目只能使用邮箱，不能查询任务结果
- 管理后台才允许看调试日志和 artifacts

## 多项目隔离模型

## 核心原则

所有对外暴露的数据都应关联 `project_id`。

建议在持久化层逐步加入：

- `project_id`
- `created_by`
- `source_app`

建议覆盖的数据对象：

- workflow task
- registration task
- mail account
- proxy lease
- session token
- service result
- artifact 元数据

## 为什么必须加 `project_id`

否则会出现以下问题：

- A 项目能查到 B 项目的邮箱账号
- A 项目能看到 B 项目的登录结果
- 无法按项目做限流、计费、审计
- 无法做资源清理和配额回收

## 任务模型建议

## 对外任务应统一成异步模型

注册、登录、注册并登录都建议支持异步任务语义：

- 创建任务
- 返回 `task_id`
- 轮询状态
- 可选 webhook 回调
- 成功后读取结果

统一状态建议：

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `timeout`

统一字段建议：

- `task_id`
- `task_type`
- `project_id`
- `status`
- `state`
- `progress`
- `result`
- `error`
- `created_at`
- `updated_at`
- `finished_at`

## 结果结构建议

建议不要把失败错误混进 `result` 字段。

统一返回结构应为：

```json
{
  "task": {},
  "result": null,
  "error": {
    "code": "...",
    "message": "..."
  }
}
```

成功时：

```json
{
  "task": {},
  "result": {},
  "error": null
}
```

这样外部项目更容易稳定接入。

## 幂等性建议

对外创建型接口建议支持：

- `Idempotency-Key`

适用接口：

- 创建注册任务
- 创建登录任务
- 创建注册并登录任务
- 创建邮箱账号
- 申请代理

作用：

- 防止重试导致重复注册
- 防止网络抖动导致资源重复创建
- 提高客户端接入稳定性

## 回调模型建议

外部项目如果只靠轮询，接入体验一般。

当前 `workflow` 已支持请求体内传入 callback 配置，平台会在任务终态时异步回调，并支持 HMAC 签名与失败重试。

建议后续继续增强：

- 请求内传入 `callback_url`
- 任务状态变化时异步回调

建议回调事件：

- `workflow.succeeded`
- `workflow.failed`
- `registration.succeeded`
- `registration.failed`
- `login.succeeded`
- `login.failed`

回调 payload 建议包含：

- `event_id`
- `event_type`
- `project_id`
- `task_id`
- `trace_id`
- `status`
- `result_summary`
- `error`
- `occurred_at`

## 限流与配额建议

共享平台必须支持按项目限流。

建议至少控制：

- 每分钟创建邮箱次数
- 每分钟发起 workflow 次数
- 同时运行中的注册任务数
- 同时租用中的代理数
- 单日成功注册上限

建议配额维度：

- `project_id`
- `api_scope`
- `provider`
- `site`

## 审计与可观测性建议

## 审计日志

建议记录：

- 谁调用了什么接口
- 对哪个 `project_id` 操作
- 创建了哪个任务
- 申请了哪个邮箱/代理
- 是否成功
- 失败原因

## Trace 传播

现有 `X-Trace-Id` 可以继续保留，并升级为统一链路追踪主键。

建议：

- 外部请求可自带 `X-Trace-Id`
- 若未传则平台生成
- 下游服务必须沿用同一 trace id

## artifacts 与 events 的开放策略

这两类接口建议默认不面向普通外部项目开放。

推荐策略：

- 普通项目：只能拿摘要错误信息
- 高权限项目：可看部分事件
- 管理员：可查看完整 events 与 artifacts

原因：

- events 可能暴露流程细节
- artifacts 可能包含页面内容、截图、token 痕迹

## 存储演进建议

## 当前阶段

SQLite 可以继续作为 MVP 存储。

适合：

- 单机部署
- 小规模并发
- 单团队使用

## 平台化阶段

如果开始给多个外部项目稳定使用，建议迁移到 PostgreSQL。

主要原因：

- 更适合多连接并发
- 更容易做索引和筛选
- 更适合按 `project_id` 查询
- 更适合审计和报表
- 更利于后续任务队列和 worker 拆分

## 推荐实施顺序

### 第一阶段：先把平台边界补齐

- 增加外部 API Key 鉴权
- 增加内部服务鉴权校验
- 为核心数据模型加入 `project_id`
- 区分公开接口与内部接口
- 修正任务详情结构中的 `result/error` 语义

### 第二阶段：补任务可靠性

- 将 daemon thread 改为持久化 worker
- 支持 retry / cancel / timeout
- 支持 webhook 回调
- 支持幂等键

### 第三阶段：补共享平台能力

- 限流
- 配额
- 审计
- 管理后台
- 项目级统计报表

### 第四阶段：补多站点能力

- login adapter 多站点化
- registration adapter 多站点化
- provider 健康度评分与自动回退

## 当前项目最推荐的对外形态

短期内建议：

- `orchestrator-service` 作为外部主入口
- `login-service` 作为独立能力入口
- `mail-service`、`proxy-service` 作为共享基础能力开放
- `workflow` 在启用 `proxy_policy` 时应自动编排代理申请与释放
- `registration-service` 先保留为内部优先

这样既能支持外部项目使用自动注册和登录能力，也不会让外部集成方直接绑定到底层易变实现。

## 下一步开发建议

建议优先做以下改造：

1. 统一 API Key 鉴权中间件
2. 给主要存储对象增加 `project_id`
3. 将公开路由和内部路由拆层
4. 补充 `workflow register/login` 独立入口
5. 修复任务详情响应结构
6. 引入可靠 worker 替代线程后台任务


## 进程模型建议

建议采用 `API Web + Worker` 双进程模型：

- Web 进程只负责鉴权、接收请求、写入任务
- Worker 进程负责从持久化队列拉取 queued 任务执行
- 服务重启后由 Worker 执行恢复与收敛


## Metrics 与心跳

建议通过内部/管理员接口暴露运行指标：

- 队列深度
- 任务状态分布
- worker 心跳时间
- 当前活跃任务


## 多 Worker 领取策略

推荐 worker 通过持久化存储进行原子 claim：

- queued 任务只能被一个 worker 成功领取
- worker 心跳需单独上报
- metrics 应展示全部 worker 心跳而非单个实例视角


## Callback Outbox

建议 callback 使用 outbox 模式：

- 先把 callback event 持久化
- 为同一业务终态生成稳定 `event_id`
- 投递失败可按 event_id 重试
- 服务重启后从 outbox 恢复未送达事件
