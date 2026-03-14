# 存储说明

当前默认使用 SQLite，数据库路径由 `APP_DB_PATH` 控制。

## 当前表
- `task_records`
- `task_events`
- `mail_accounts`
- `proxy_leases`
- `session_tokens`
- `service_results`
- `artifacts`

## 文件落盘
artifact 文件由 `ARTIFACTS_STORAGE_PATH` 控制，当前会落盘到：

```text
<ARTIFACTS_STORAGE_PATH>/<store_name>/<task_id>/
```

例如：
```text
/tmp/aiapi_tool_artifacts/registration_service/<task_id>/autoregister.json
```
