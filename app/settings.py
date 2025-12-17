from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str
    ADMIN_ID: int
    
    # Web / Admin
    ADMIN_PASSWORD: str = "admin" # Default password for initial setup
    SECRET_KEY: str = "change_me_super_secret" # For session/JWT
    
    # Database
    RELAYCAT_DB_URL: str = "sqlite+aiosqlite:///data/relaycat.db"
    
    # Feature Flags
    ENABLE_FORWARDING: bool = True
    
    class Config:
        env_file = ".env"
        env_prefix = "RELAYCAT_"

settings = Settings()
