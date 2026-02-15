from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


    APP_NAME: str = "MEDI"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str
    OPENAI_EMBED_MODEL: str
    OPENAI_API_KEY: str

    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_WHATSAPP_NUMBER: str | None = None
    MENU_TEMPLATE_SID: str | None = None

    LLM_PROVIDER: str = "anthropic"
    USE_LLM: bool = True
    LLM_MAX_HISTORY: int = 12

    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str | None = None

settings = Settings()