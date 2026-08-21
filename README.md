# RelayCat

轻量的 Telegram 私聊中继与滥用防护服务。用户向 Bot 发消息，管理员直接在 Telegram 中回复；Web 后台用于管理规则、限流、封禁和安全日志。

![RelayCat](./app/static/images/relaycat-logo.png)

## 功能

- 双向中继文本、图片、文件、贴纸等 Telegram 消息。
- 服务端一次性人机验证，绑定用户、会话和消息并限制重试。
- 关键词、正则、链接、转发状态、发送者和消息类型规则。
- Unicode 混淆归一化、短时突发检测、重复消息检测和自动封禁。
- 版本化预置规则；未修改的预置会随版本自动升级。
- 安全日志不保存消息正文。
- SQLite / PostgreSQL 与 Docker 部署。

## 部署

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
RELAYCAT_BOT_TOKEN=123456789:your-bot-token
RELAYCAT_ADMIN_ID=123456789
RELAYCAT_ADMIN_PASSWORD=your-strong-password
RELAYCAT_SECRET_KEY=your-long-random-secret
RELAYCAT_PORT=8765
```

生成随机 `RELAYCAT_SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

启动官方镜像：

```bash
docker compose pull
docker compose up -d
```

镜像：`ghcr.io/yayitinyu/relaycat:latest`

后台地址为 `http://服务器地址:8765/login`，健康检查为 `/healthz`。公网部署应使用 HTTPS，并设置：

```dotenv
RELAYCAT_COOKIE_SECURE=true
```

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RELAYCAT_BOT_TOKEN` | 无 | BotFather 提供的 Token |
| `RELAYCAT_ADMIN_ID` | 无 | 管理员 Telegram 数字 ID |
| `RELAYCAT_ADMIN_PASSWORD` | `admin` | Web 后台密码，生产环境必须修改 |
| `RELAYCAT_SECRET_KEY` | 不安全的开发值 | Session 与内容指纹密钥，生产环境必须修改 |
| `RELAYCAT_HOST` | `0.0.0.0` | Web 监听地址 |
| `RELAYCAT_PORT` | `8765` | Web 与 Compose 映射端口 |
| `RELAYCAT_COOKIE_SECURE` | `false` | HTTPS 部署时设为 `true` |
| `RELAYCAT_DATA_DIR` | `./data` | SQLite 数据目录 |
| `RELAYCAT_DB_URL` | SQLite | SQLAlchemy 异步数据库 URL |
| `RELAYCAT_ENABLE_FORWARDING` | `true` | 是否启用消息中继 |
| `RELAYCAT_DROP_PENDING_UPDATES` | `false` | 启动时是否丢弃旧 Telegram 更新 |

PostgreSQL 示例：

```dotenv
RELAYCAT_DB_URL=postgresql+asyncpg://relaycat:password@postgres:5432/relaycat
```

## 更新与数据

SQLite 位于 Compose volume `relaycat-data`。升级前备份该 volume；不要在服务运行时覆盖数据库文件。

```bash
docker compose pull
docker compose up -d
```

启动时会执行兼容迁移并清理过期安全日志。编辑过的预置规则会脱离自动更新，避免覆盖自定义内容。

## 本地开发

需要 Python 3.12+：

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

验证：

```bash
python -m ruff check app tests
python -m unittest discover -s tests -v
python -m compileall -q app
docker compose config
```

## 镜像构建

GitHub Actions 在 GitHub-hosted 原生 runner 上分别构建：

- `linux/amd64`：`ubuntu-24.04`
- `linux/arm64`：`ubuntu-24.04-arm`

两个架构按 digest 推送到 GHCR，再合成为 `ghcr.io/yayitinyu/relaycat:latest`。Pull Request 只构建验证，不推送镜像。

## License

[MIT](./LICENSE)
