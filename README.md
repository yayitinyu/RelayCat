# RelayCat (Python Version)

> 一个用于 Telegram 的轻量级“双向消息中继 Bot”，主打：**双向转发、防骚扰、Web 管理面板、免 reCAPTCHA**

RelayCat 是一个基于 Python (Aiogram + FastAPI) 重写的 Telegram 机器人，用于将用户私聊消息转发给管理员，支持管理员直接回复。

[English Version](./README_en.md)

---

## 🚀 特性

- **双向消息中继**
  - 用户私聊 Bot，消息自动转发给管理员。
  - 管理员回复转发的消息（或信息卡片），内容自动发回给用户。
  - 支持文本、图片、文件、贴纸等多种消息类型。

- **原生防骚扰验证 (无 reCAPTCHA)**
  - 摒弃了复杂的网页 reCAPTCHA。
  - 采用 **Telegram 原生 In-Chat 验证**（Emoji 点击挑战）。
  - 用户首次使用时需点击正确的 Emoji 进行人机验证，体验更流畅。

- **Web 管理面板**
  - 内置 FastAPI 管理后台。
  - **仪表盘**：查看用户总数、消息统计。
  - **用户管理**：查看最近用户，一键封禁/解封。
  - 默认地址：`http://localhost:8080/`

- **数据持久化**
  - 默认使用 **SQLite** (`relaycat.db`)，无需配置额外数据库。
  - 支持通过 SQLAlchemy 切换到 **PostgreSQL** 或 MySQL。

- **Docker 一键部署**
  - 提供 `docker-compose.yml`，开箱即用。

---

## 🛠️ 快速开始 (Docker)

1. **克隆项目**
   ```bash
   git clone https://github.com/your-repo/RelayCat.git
   cd RelayCat
   ```

2. **配置环境变量**
   修改 `docker-compose.yml` 或创建 `.env` 文件：

   ```yaml
   environment:
     - RELAYCAT_BOT_TOKEN=your_bot_token_here       # 你的 Bot Token
     - RELAYCAT_ADMIN_ID=123456789                  # 你的 Telegram ID
     - RELAYCAT_ADMIN_PASSWORD=secure_password      # 管理面板登录密码
     - RELAYCAT_SECRET_KEY=change_me_to_random      # Session 加密密钥
   ```

3. **启动服务**
   ```bash
   docker compose up -d --build
   ```

4. **开始使用**
   - **Bot**: 给你的机器人发送 `/start`。
   - **Admin Web**: 浏览器访问 `http://ip:8080/login` 进入管理面板。

---

## ⚙️ 配置说明

所有配置均通过环境变量管理（支持 `.env` 文件）。

| 变量名 | 必填 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `RELAYCAT_BOT_TOKEN` | ✅ | - | Telegram Bot Token (从 @BotFather 获取) |
| `RELAYCAT_ADMIN_ID` | ✅ | - | 管理员的数字 ID |
| `RELAYCAT_ADMIN_PASSWORD` | ❌ | `admin` | Web 管理面板的登录密码 |
| `RELAYCAT_SECRET_KEY` | ❌ | `change_me` |用于加密 Session Cookie 的密钥 |
| `RELAYCAT_DB_URL` | ❌ | `sqlite+aiosqlite:////data/relaycat.db` | 数据库连接字符串 (支持 PostgreSql) |
| `RELAYCAT_ENABLE_FORWARDING` | ❌ | `True` | 是否开启消息转发功能 |

---

## 🧩 架构

本项目从旧版 PHP 架构完全重写为现代 Python 异步架构：

- **Bot 框架**: `aiogram 3.x` (异步高效)
- **Web 框架**: `FastAPI` (用于管理面板)
- **数据库 ORM**: `SQLAlchemy 2.0` (异步)
- **运行器**: `uvicorn` (ASGI 服务器)

所有组件运行在同一个进程中（通过 asyncio 并发），既节省资源又方便部署。

---

## 📝 开发与运行 (非 Docker)

如果你想在本地开发：

1. **安装依赖**
   (推荐使用 venv)
   ```bash
   pip install -r requirements.txt
   ```

2. **设置环境变量**
   在项目根目录创建 `.env` 文件：
   ```ini
   RELAYCAT_BOT_TOKEN=xxx
   RELAYCAT_ADMIN_ID=xxx
   ```

3. **启动**
   ```bash
   python -m app.main
   ```

---

## 📄 许可证

MIT License
