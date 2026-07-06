from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RELAYCAT_",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    bot_token: SecretStr
    admin_id: int
    drop_pending_updates: bool = False

    # Web
    host: str = "0.0.0.0"
    port: int = 8765
    admin_password: SecretStr = SecretStr("admin")
    secret_key: SecretStr = SecretStr("change-me-before-production")
    cookie_secure: bool = False

    # Database
    data_dir: str = "./data"
    db_url: str | None = None

    # Relay
    enable_forwarding: bool = True

    # OpenAI-compatible Chat Completions API
    ai_enabled: bool = False
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: SecretStr | None = None
    ai_model: str = "gpt-4o-mini"
    ai_system_prompt: str = Field(
        default=(
            "你是账号主人的聊天助理。请使用与对方相同的语言，简洁、礼貌地回复。"
            "不要编造事实、承诺付款或泄露隐私；不确定时说明需要账号主人确认。"
        )
    )
    ai_timeout_seconds: float = 30.0
    ai_history_limit: int = 12

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("ai_history_limit")
    @classmethod
    def validate_history_limit(cls, value: int) -> int:
        if not 2 <= value <= 50:
            raise ValueError("ai_history_limit must be between 2 and 50")
        return value

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        normalized_dir = self.data_dir.replace("\\", "/")
        return f"sqlite+aiosqlite:///{normalized_dir}/relaycat.db"


settings = Settings()
