# Provider 说明

## 当前邮箱 providers

### `gptmail`
- 类型：API
- 状态：已接入
- 当前优先级：1
- 已验证：
  - 创建邮箱
  - `checkalias` 通过
  - 收到 `Welcome to chayns!`
  - 提取确认链接

### `moemail`
- 类型：API
- 状态：已接入
- 当前优先级：2
- 已验证：
  - 创建邮箱
  - `checkalias` 通过
  - 收到 `Welcome to chayns!`
  - 提取确认链接

### `duckmail`
- 类型：API
- 状态：已接入
- 当前优先级：3
- 说明：部分域名可用，但波动较大

### `smailpro_api`
- 类型：API
- 状态：已接入
- 当前优先级：4
- 说明：依赖额度，当前 key 可能受限

### `smailpro_web`
- 类型：网页自动化
- 状态：已接入
- 当前优先级：5
- 说明：
  - 通过 Selenium 驱动 `https://smailpro.com/temporary-email`
  - 已支持 `list_domains()`，会动态解析页面内的域名与 server 配置
  - 已支持增强版 `health_check()`，会同时检查页面可访问性、域名配置解析和浏览器可用性
  - `headless` 模式下若命中站点 `Captcha is invalid` 风控，会自动回退到 xvfb 下的非 `headless` 浏览器重试（默认开启）
  - 当前已验证：
    - 非 `headless` 模式可正常创建邮箱与查询收件箱
    - `headless` 模式可通过自动回退机制完成创建

### `mailcx`
- 类型：API
- 状态：已接入
- 当前优先级：6
- 说明：当前目标场景下收信不稳定
