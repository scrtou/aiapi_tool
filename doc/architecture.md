# 当前架构说明

## 服务组成

当前项目包含以下服务：

- `mail-service`
  - 创建邮箱
  - 获取邮件列表
  - 获取单封邮件内容
  - 提取确认链接
- `proxy-service`
  - 当前为静态代理池骨架
- `registration-service`
  - 执行 `chayns` 注册任务
  - 负责页面驱动、验证码处理、邮件确认、设密码、建立登录态
- `login-service`
  - 执行 `chayns` 登录
  - 策略为 `API 优先，UI 兜底`
- `orchestrator-service`
  - 面向外部的工作流入口
  - 负责通过 HTTP 编排其它服务完成 `register-and-login`

## 当前主链路

```text
gptmail -> chayns register API -> confirmation email -> register/verify -> login API
```

## 目录结构

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

## 当前运行时关系

```text
Client
  -> orchestrator-service
       -> mail-service
       -> registration-service
       -> login-service
```

`registration-service` 当前也会通过 `mail-service` 获取邮件，不再直接依赖旧单体目录。
