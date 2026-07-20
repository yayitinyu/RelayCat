# RelayCat

A lightweight Telegram bidirectional relay and Business AI chat assistant. It forwards Bot conversations to an administrator, filters abusive or spam traffic, and can act as an official Connected Business Bot through an OpenAI-compatible API.

[中文文档](./README.md)

## Quick start

```bash
cp .env.example .env
# Set BOT_TOKEN, ADMIN_ID, ADMIN_PASSWORD and SECRET_KEY.
docker compose pull
docker compose up -d
```

The dashboard listens on port `8765` by default. Change `RELAYCAT_PORT` in `.env` if the port is occupied; the application and Compose mapping use the same value.

## Telegram Business AI

1. Enable **Business Mode** for the bot in `@BotFather`.
2. Connect it under **Telegram Business → Chatbots** and grant reply access.
3. Configure an OpenAI-compatible Chat Completions endpoint:

```dotenv
RELAYCAT_AI_ENABLED=true
RELAYCAT_AI_BASE_URL=https://api.openai.com/v1
RELAYCAT_AI_API_KEY=your-api-key
RELAYCAT_AI_MODEL=gpt-4o-mini
```

4. Restart RelayCat and edit the system prompt in **Automation settings**.

RelayCat uses Telegram's official business connection API. It never logs in as a user or stores a phone number, login code, or user session. The dashboard can configure the AI Base URL, model, and API key. Dashboard keys are encrypted at rest with `RELAYCAT_SECRET_KEY`, are never displayed again, and remote HTTP endpoints are rejected in favor of HTTPS.

## Message protection

- Beginner-friendly keyword, sender, link, forwarding, command, and message-type rules, with custom regular expressions kept in the advanced section.
- Optional AI-assisted moderation for messages that did not match an explicit allow rule.
- Per-user messages-per-minute limits and automatic temporary or permanent bans after repeated interceptions.
- A dashboard audit trail for relay, filtering, AI, rate-limit, ban, and settings events. Message bodies and API keys are not stored in the log; retention defaults to 30 days.

Official documentation: [Connected Business Bots](https://core.telegram.org/api/bots/connected-business-bots) and [Bot API](https://core.telegram.org/bots/api#businessconnection).

## Container

The GitHub Actions workflow publishes:

- `ghcr.io/yayitinyu/relaycat:latest`
- immutable `ghcr.io/yayitinyu/relaycat:sha-*` rollback tags

The image supports `linux/amd64` and `linux/arm64`. SQLite data is stored in the `relaycat-data` volume.

## Development

Python 3.12+ is recommended.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

See the [Chinese README](./README.md) for the complete configuration, backup, proxy, security, and recovery guide.

## License

[MIT](./LICENSE)
