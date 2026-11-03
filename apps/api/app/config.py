from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://commerceradar:commerceradar@localhost:5432/commerceradar"
    redis_url: str = "redis://localhost:6379/0"
    # Identify ourselves honestly to sites; add a contact URL once the public domain is live.
    bot_user_agent: str = "CommerceRadarBot/0.2"


settings = Settings()
