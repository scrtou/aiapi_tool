# 运行流程说明

## 注册流程

当前 `registration-service` 的 `chayns` 主路径：

1. 使用传入邮箱账号进入目标站点
2. 输入邮箱并推进到 `/setup`
3. 检测新用户分支
4. 填写姓名
5. 生成并校验 `mCaptcha` token
6. 直接调用注册 API
7. 轮询邮箱服务获取验证邮件
8. 提取确认链接中的 `code`
9. 直接调用 `register/verify`
10. 建立站点登录态
11. 提取 `userid/personid/token`

## 登录流程

当前 `login-service` 的 `chayns` 主路径：

1. 调用 `https://auth.tobit.com/v2/token`
2. 获取正式 `type=1` token
3. 从 JWT 解析用户标识
4. 查询 `has_pro_access`
5. 返回统一 `LoginResult`

## 编排流程

当前 `orchestrator-service` 的 `register-and-login`：

1. 通过 `mail-service` 创建邮箱
2. 通过 `registration-service` 注册
3. 通过 `login-service` 登录
4. 聚合 `registration + login` 结果
5. 返回 workflow 结果

## Nexos 注册流程

当前 `registration-service` 的 `nexos` 主路径：

1. 调用 Ory native registration flow 创建注册会话
2. 提交邮箱与姓名，推进到密码步骤
3. 获取 Cloudflare Turnstile token（外部传入 / 2captcha / capsolver / 浏览器回退）
4. 提交密码完成账号创建
5. 调用 verification flow 触发验证码邮件
6. 轮询 `mail-service` 收取验证码邮件
7. 提交验证码完成邮箱验证
8. 调用 Ory native login flow 登录
9. 通过 `whoami` 拉取 identity / session 信息
10. 返回统一 `RegistrationResult`

## Nexos 登录流程

当前 `login-service` 的 `nexos` 主路径：

1. 调用 Ory native login flow 创建登录会话
2. 提交邮箱密码
3. 提取 `session_token` 或 cookie session
4. 调用 `whoami` 校验登录态
5. 返回统一 `LoginResult`

当 `registration_mode=drission` / `login_mode=drission` 时：

1. 使用 `DrissionPage` 打开 Nexos 登录/注册页面
2. 按页面真实顺序完成 Turnstile、表单提交、邮件确认
3. 通过浏览器 Cookies 构造 session handle
4. 调用 `whoami` 校验登录态
5. 返回统一结果
