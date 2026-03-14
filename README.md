# aiapi_tool

当前项目是一个账号自动化服务平台，已完成 `mail-service`、`proxy-service`、`registration-service`、`login-service`、`orchestrator-service` 五个服务的 MVP 实现。

已新增 `nexos` 站点适配：`login-service` 支持 `nexos` 原生 API 登录与 `DrissionPage` 浏览器登录，`registration-service` 支持 `nexos` 注册 + 邮箱验证 + 登录。

注意：`nexos` 注册受 Cloudflare Turnstile 保护，需提供 `strategy.captcha.turnstile_token`，或配置 `2captcha/capsolver`，也可将 `registration_mode/login_mode` 设为 `drission` 走浏览器完整流程。

## 当前推荐主链路

```text
gptmail -> chayns register API -> confirmation email -> register/verify -> login API
```

## 项目结构

```text
libs/
  contracts/
  core/
  clients/
services/
  mail_service/
  proxy_service/
  registration_service/
  login_service/
  orchestrator_service/
doc/
data/
```

## 文档

详见 `doc/`：
- `doc/README.md`
- `doc/architecture.md`
- `doc/api-reference.md`
- `doc/runtime-flows.md`
- `doc/providers.md`
- `doc/configuration.md`
- `doc/storage.md`
- `doc/operations.md`

## 启动

```bash
cp .env.example .env
docker compose up --build
```

## 端口
- `8000`: orchestrator-service
- `8001`: mail-service
- `8002`: proxy-service
- `8003`: registration-service
- `8004`: login-service
