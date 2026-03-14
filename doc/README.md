# aiapi_tool 文档

当前项目是一个面向账号自动化的服务化平台，已完成 `mail-service`、`proxy-service`、`registration-service`、`login-service`、`orchestrator-service` 五个服务的 MVP 实现。

## 文档目录

- `architecture.md`
  - 当前项目整体架构与服务关系
- `api-reference.md`
  - 当前可用 API 列表与请求/响应说明
- `platform-api-design.md`
  - 对外平台 API、鉴权、租户隔离与开放策略设计
- `runtime-flows.md`
  - 注册、登录、工作流的实际运行流程
- `providers.md`
  - 当前已接入邮箱 provider 的能力与推荐顺序
- `configuration.md`
  - 环境变量与服务配置说明
- `storage.md`
  - SQLite 持久化模型与数据目录说明
- `operations.md`
  - 启动、部署、排障与运维说明
  - 当前统一错误码说明
