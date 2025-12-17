# RelayCat (Python Version)

> A lightweight "Bidirectional Message Relay Bot" for Telegram. Features: **Bidirectional Forwarding, Anti-Spam, Web Admin Panel, No reCAPTCHA**.

RelayCat has been rewritten in Python (Aiogram + FastAPI) to forward private messages from users to the admin, allowing the admin to reply directly.

[中文文档](./README.md)

---

## 🚀 Features

- **Bidirectional Relay**
  - Users send private messages to the Bot -> Forwarded to Admin.
  - Admin replies to the forwarded message (or info card) -> Sent back to the User.
  - Supports Text, Photos, Files, Stickers, etc.

- **Native Verification (No reCAPTCHA)**
  - Replaced the complex web-based Google reCAPTCHA.
  - Uses **Telegram Native In-Chat Verification** (Emoji Challenge).
  - Users must click the correct Emoji to verify they are human before sending messages.

- **Web Admin Panel**
  - Built-in FastAPI admin dashboard.
  - **Dashboard**: View user counts and message stats.
  - **User Management**: View recent users, Ban/Unban users easily.
  - Default URL: `http://localhost:8080/`

- **Database Persistence**
  - Uses **SQLite** (`relaycat.db`) by default. No complex setup required.
  - Supports **PostgreSQL** or MySQL via SQLAlchemy configuration.

- **Docker Ready**
  - Includes `docker-compose.yml` for instant deployment.

---

## 🛠️ Quick Start (Docker)

1. **Clone the Repo**
   ```bash
   git clone https://github.com/your-repo/RelayCat.git
   cd RelayCat
   ```

2. **Configure Environment**
   Edit `docker-compose.yml` or create a `.env` file:

   ```yaml
   environment:
     - RELAYCAT_BOT_TOKEN=your_bot_token_here       # Your Bot Token
     - RELAYCAT_ADMIN_ID=123456789                  # Your Telegram numeric ID
     - RELAYCAT_ADMIN_PASSWORD=secure_password      # Password for Admin Panel
     - RELAYCAT_SECRET_KEY=change_me_to_random      # Secret key for sessions
   ```

3. **Start Services**
   ```bash
   docker compose up -d --build
   ```

4. **Usage**
   - **Bot**: Send `/start` to your bot.
   - **Admin Web**: Visit `http://ip:8080/login` to manage the bot.

---

## ⚙️ Configuration

All settings are managed via Environment Variables.

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `RELAYCAT_BOT_TOKEN` | ✅ | - | Telegram Bot Token (from @BotFather) |
| `RELAYCAT_ADMIN_ID` | ✅ | - | Admin's Numeric ID |
| `RELAYCAT_ADMIN_PASSWORD` | ❌ | `admin` | Password for Web Admin Panel |
| `RELAYCAT_SECRET_KEY` | ❌ | `change_me` | Secret key for session encryption |
| `RELAYCAT_DB_URL` | ❌ | `sqlite+aiosqlite:////data/relaycat.db` | Database URL (PostgreSql supported) |
| `RELAYCAT_ENABLE_FORWARDING` | ❌ | `True` | Enable message forwarding |

---

## 🧩 Architecture

This project is a complete rewrite from the legacy PHP version to a modern Python Async stack:

- **Bot Framework**: `aiogram 3.x` (Async)
- **Web Framework**: `FastAPI` (Admin Panel)
- **Database ORM**: `SQLAlchemy 2.0` (Async)
- **Server**: `uvicorn` (ASGI)

All components run in a single process (concurrency via asyncio), making it resource-efficient and easy to deploy.

---

## 📝 Local Development

If you want to run it without Docker:

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set Environment Variables**
   Create a `.env` file in the root directory:
   ```ini
   RELAYCAT_BOT_TOKEN=xxx
   RELAYCAT_ADMIN_ID=xxx
   ```

3. **Run**
   ```bash
   python -m app.main
   ```

---

## 📄 License

MIT License
