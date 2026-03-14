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
- 说明：当前受站点 `Captcha is invalid` 风控影响，不作为主链路

### `mailcx`
- 类型：API
- 状态：已接入
- 当前优先级：6
- 说明：当前目标场景下收信不稳定
