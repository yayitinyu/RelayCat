# RelayCat

轻量的 Telegram 双向消息中继与 Business AI 聊天助理。用户可以给 Bot 发消息，由管理员在 Telegram 内直接回复；也可以把 Bot 连接到 Telegram Business 账号，让它代表账号处理私聊并通过 OpenAI-compatible API 自动回复。

[English](./README_en.md)

![RelayCat logo](./app/static/images/relaycat-logo.png)

## 功能

- 双向消息中继：支持文本、图片、文件、贴纸等 Telegram 消息类型。
- 原生人机验证：首次会话通过 Emoji 按钮完成验证，无需 reCAPTCHA。
- Telegram Business 自动化：Bot 可作为 Connected Business Bot 代表账号回复私聊。
- AI 自动回复：兼容 OpenAI Chat Completions 格式，可自定义 Base URL、模型与系统提示词。
- 消息过滤：按消息内容、用户名、命令或转发状态执行正则规则。
- Web 管理后台：查看统计、封禁用户、管理规则和 AI 提示词。
- 轻量持久化：默认使用 SQLite，也可切换 PostgreSQL。
- Docker / GHCR：自动发布 `ghcr.io/yayitinyu/relaycat:latest`，同时保留 `sha-*` 回滚标签。

## 快速部署

### 1. 准备配置

```bash
cp .env.example .env
```

至少修改以下配置：

```dotenv
RELAYCAT_BOT_TOKEN=123456789:your-bot-token
RELAYCAT_ADMIN_ID=123456789
RELAYCAT_ADMIN_PASSWORD=your-strong-password
RELAYCAT_SECRET_KEY=your-long-random-secret
RELAYCAT_PORT=8765
```

`RELAYCAT_PORT` 同时控制应用监听端口和 Docker 主机映射。若 `8765` 被占用，改成例如 `9180` 即可，不需要修改代码或 Compose 文件。

可以用下面的命令生成随机 `SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2. 启动

使用 GHCR 的 Latest 镜像：

```bash
docker compose pull
docker compose up -d
```

从本地源码构建：

```bash
docker compose up -d --build
```

打开 `http://服务器IP:8765/login`。如果修改过端口，请替换为新的端口。

### 3. 检查状态

```bash
docker compose ps
docker compose logs -f --tail=100 relaycat
```

健康检查地址为 `/healthz`。

## Telegram Business / Secretary Bot

RelayCat 使用 Telegram 官方的 Connected Business Bots，不会要求登录个人账号，也不需要保存手机号、验证码或用户会话。

1. 在 `@BotFather` 中为 Bot 打开 **Business Mode**。
2. 在支持 Telegram Business 的账号中打开 **设置 → Telegram Business → Chatbots**。
3. 连接 RelayCat Bot，选择允许处理的聊天，并授予回复消息权限。
4. 在 `.env` 中配置 AI API：

   ```dotenv
   RELAYCAT_AI_ENABLED=true
   RELAYCAT_AI_BASE_URL=https://api.openai.com/v1
   RELAYCAT_AI_API_KEY=your-api-key
   RELAYCAT_AI_MODEL=gpt-4o-mini
   ```

5. 重启服务，在后台的「自动化设置」中确认 AI 状态，并编辑系统提示词。

```bash
docker compose restart relaycat
```

当前自动回复范围：

- 只处理 Connected Business Bot 收到的私聊文本或媒体 caption。
- 账号本人发送的消息不会触发 AI，避免重复回复。
- 同一聊天串行生成回复，并保存最近上下文到数据库。
- API 超时、无权限或返回格式异常时只记录错误，不会把内部错误发给聊天对象。
- Telegram 对 Business Bot 回复权限和可回复时间范围有平台限制。

官方说明：[Connected Business Bots](https://core.telegram.org/api/bots/connected-business-bots) / [Bot API](https://core.telegram.org/bots/api#businessconnection)

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RELAYCAT_BOT_TOKEN` | 无 | 必填，BotFather 提供的 Bot Token |
| `RELAYCAT_ADMIN_ID` | 无 | 必填，管理员 Telegram 数字 ID |
| `RELAYCAT_ADMIN_PASSWORD` | `admin` | 管理后台密码，生产环境必须修改 |
| `RELAYCAT_SECRET_KEY` | 不安全的开发值 | Cookie 签名密钥，生产环境必须修改 |
| `RELAYCAT_HOST` | `0.0.0.0` | Web 监听地址 |
| `RELAYCAT_PORT` | `8765` | Web 监听与 Compose 映射端口 |
| `RELAYCAT_COOKIE_SECURE` | `false` | 通过 HTTPS 访问时应设为 `true` |
| `RELAYCAT_DATA_DIR` | `./data` | SQLite 数据目录 |
| `RELAYCAT_DB_URL` | SQLite | SQLAlchemy 异步数据库 URL |
| `RELAYCAT_ENABLE_FORWARDING` | `true` | 是否启用普通 Bot 消息中继 |
| `RELAYCAT_DROP_PENDING_UPDATES` | `false` | 启动时是否丢弃 Telegram 未处理更新 |
| `RELAYCAT_AI_ENABLED` | `false` | Business AI 默认开关，可在后台覆盖 |
| `RELAYCAT_AI_BASE_URL` | OpenAI API | OpenAI-compatible API Base URL |
| `RELAYCAT_AI_API_KEY` | 无 | AI API 密钥，只从环境变量读取 |
| `RELAYCAT_AI_MODEL` | `gpt-4o-mini` | Chat Completions 模型名 |
| `RELAYCAT_AI_SYSTEM_PROMPT` | 内置安全提示词 | AI 默认系统提示词，可在后台覆盖 |
| `RELAYCAT_AI_TIMEOUT_SECONDS` | `30` | AI 请求超时秒数 |
| `RELAYCAT_AI_HISTORY_LIMIT` | `12` | 每次生成携带的最近消息数，范围 2–50 |

PostgreSQL 示例：

```dotenv
RELAYCAT_DB_URL=postgresql+asyncpg://relaycat:password@postgres:5432/relaycat
```

## 项目结构

```text
app/
├── bot/                 # 普通中继、验证与 Business 自动化
├── core/                # 环境配置和会话签名
├── database/            # SQLAlchemy 模型与连接
├── services/            # AI 客户端和运行时设置
├── static/              # Logo 与后台样式
├── templates/           # Jinja2 管理页面
├── web/                 # 管理后台路由
└── main.py              # FastAPI 与 Bot 生命周期入口
```

FastAPI 和 aiogram polling 运行在同一 asyncio 进程中，适合低配 VPS。默认无需 Redis 或独立数据库服务。

## 本地开发

需要 Python 3.12+：

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

修改代码后建议执行：

```bash
python -m compileall -q app
python -m unittest discover -s tests -v
docker compose config
docker build -t relaycat:local .
```

## 数据、备份与恢复

Compose 使用 `relaycat-data` volume 保存 SQLite，不会把数据只放在容器文件层。

备份前先短暂停止服务，避免复制正在写入的 SQLite：

```bash
docker compose stop relaycat
docker run --rm -v relaycat_relaycat-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/relaycat-data.tar.gz -C /data .
docker compose start relaycat
```

恢复时先停止服务并备份当前 volume，再将压缩包解压到 `/data`。不要在运行中的数据库上直接覆盖文件。

## 反向代理与安全

- 对公网开放时建议使用 Caddy 或 Nginx 提供 HTTPS，并将 `RELAYCAT_COOKIE_SECURE=true`。
- 只开放反向代理端口；通过防火墙限制后台端口的直接访问。
- 不要提交 `.env`，也不要把 Bot Token、API Key、密码或数据库连接串写入镜像。
- AI 提示词和开关存于数据库；AI API Key 仅保存在运行环境。
- Cloudflare 代理时请确认源站端口受支持，或由 443 反代到 RelayCat。

## 容器发布

`.github/workflows/docker-publish.yml` 在 `main` / `master` 推送时构建 `linux/amd64` 和 `linux/arm64` 镜像：

- `ghcr.io/yayitinyu/relaycat:latest`：日常部署标签。
- `ghcr.io/yayitinyu/relaycat:sha-xxxxxxx`：不可变回滚标签。

Pull Request 只执行构建验证，不推送镜像。

## License

[MIT](./LICENSE)
