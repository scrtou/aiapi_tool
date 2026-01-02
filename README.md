# aiapi-tool - Account Login Service

自动化账户登录服务，为 aiapi 提供账户认证支持。

## 功能

- 使用 Selenium 自动化浏览器登录
- 获取账户认证 token
- 提供 RESTful API 接口

## 快速启动

```bash
docker compose up --build -d
```

服务将在 `http://localhost:5557` 启动。

## API 文档

启动服务后访问：http://localhost:5557/docs

### 主要端点

- `POST /aichat/chayns/login` - 登录并获取 token

**请求示例:**

```bash
curl -X POST http://localhost:5557/aichat/chayns/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "user@example.com",
    "password": "password123"
  }'
```

**响应示例:**

```json
{
  "email": "user@example.com",
  "userid": 12345,
  "personid": "ABC123",
  "token": "eyJhbGc..."
}
```

## 部署

### 本地部署

```bash
docker compose up -d
```

### 远程部署

```bash
# 使用自定义端口
docker compose up -d
# 或直接运行
docker run -p 5557:5557 aiapi-tool
```

### 使用 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name login.example.com;

    location / {
        proxy_pass http://localhost:5557;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 环境要求

- Docker
- 2GB+ RAM（用于 Chrome 浏览器）

## 技术栈

- FastAPI - Web 框架
- Selenium - 浏览器自动化
- Chrome - 无头浏览器

