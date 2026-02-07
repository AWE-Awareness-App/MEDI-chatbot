from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENV: str = "local"
    APP_NAME: str = "medi"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql+psycopg2://postgres:password@localhost:5432/medi"

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
