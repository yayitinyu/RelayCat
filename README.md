# RelayCat

轻量的 Telegram 双向消息中继与 Business AI 聊天助理。用户可以给 Bot 发消息，由管理员在 Telegram 内直接回复；也可以把 Bot 连接到 Telegram Business 账号，让它代表账号处理私聊并通过 OpenAI-compatible API 自动回复。

[English](./README_en.md)

![RelayCat logo](./app/static/images/relaycat-logo.png)

## 功能

- 双向消息中继：支持文本、图片、文件、贴纸等 Telegram 消息类型。
- 原生人机验证：首次会话通过 Emoji 按钮完成验证，无需 reCAPTCHA。
- Telegram Business 自动化：Bot 可作为 Connected Business Bot 代表账号回复私聊。
- AI 自动回复：兼容 OpenAI Chat Completions 格式，可自定义 Base URL、模型与系统提示词。
- 分层消息防护：关键词、发送者名称、链接、命令、转发状态、消息类型与限时正则规则。
- AI 辅助审查：使用 OpenAI-compatible 接口对未命中白名单的文字做第二层安全判断。
- 自动限流与封禁：限制每人每分钟发送量，并在短时间多次拦截后临时或永久封禁。
- 安全日志：记录中继、拦截、AI、限流、封禁和后台设置事件，不保存消息正文或密钥。
- Web 管理后台：查看统计、封禁用户、使用推荐防护管理规则，并配置安全与 AI。
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
4. 在管理后台的「安全与 AI」中配置 Base URL、模型和 API Key。也可以继续使用 `.env` 作为初始值或回退配置：

   ```dotenv
   RELAYCAT_AI_ENABLED=true
   RELAYCAT_AI_BASE_URL=https://api.openai.com/v1
   RELAYCAT_AI_API_KEY=your-api-key
   RELAYCAT_AI_MODEL=gpt-4o-mini
   ```

5. 打开 Business AI 助理开关并编辑职责与语气。后台设置立即生效，无需为普通设置重启服务。

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
| `RELAYCAT_AI_ENABLED` | `false` | Business AI 初始开关，可在后台覆盖 |
| `RELAYCAT_AI_BASE_URL` | OpenAI API | OpenAI-compatible API Base URL 初始值 |
| `RELAYCAT_AI_API_KEY` | 无 | AI API 密钥回退值，也可在后台加密保存 |
| `RELAYCAT_AI_MODEL` | `gpt-4o-mini` | Chat Completions 模型初始值 |
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

规则、安全设置、加密后的后台 AI Key 与日志都存放在同一数据库中。升级旧版本时，RelayCat 会自动补齐新增字段与日志表；安全日志默认保留 30 天，可在后台调整为 1–365 天。

## 反向代理与安全

- 对公网开放时建议使用 Caddy 或 Nginx 提供 HTTPS，并将 `RELAYCAT_COOKIE_SECURE=true`。
- 只开放反向代理端口；通过防火墙限制后台端口的直接访问。
- 不要提交 `.env`，也不要把 Bot Token、API Key、密码或数据库连接串写入镜像。
- 后台 AI API Key 使用 `RELAYCAT_SECRET_KEY` 派生的 Fernet 密钥加密后存入数据库，页面永不回显；使用默认 Session Secret 时禁止后台保存 Key。
- 远程 AI Base URL 必须使用 HTTPS，本机 `localhost` / `127.0.0.1` / `::1` 才允许 HTTP。
- AI 审查只发送最多 4000 字符的消息文本，不发送 Telegram Token、管理员密码或历史日志；接口失败时记录错误并按设置放行。
- 管理后台所有已登录写操作都校验 CSRF Token，Session Cookie 使用 `HttpOnly` 和 `SameSite=Strict`；通过 HTTPS 时还应设置 `RELAYCAT_COOKIE_SECURE=true`。
- Cloudflare 代理时请确认源站端口受支持，或由 443 反代到 RelayCat。

## 容器发布

`.github/workflows/docker-publish.yml` 在 `main` / `master` 推送时构建 `linux/amd64` 和 `linux/arm64` 镜像：

- `ghcr.io/yayitinyu/relaycat:latest`：日常部署标签。
- `ghcr.io/yayitinyu/relaycat:sha-xxxxxxx`：不可变回滚标签。

Pull Request 只执行构建验证，不推送镜像。

## License

[MIT](./LICENSE)
