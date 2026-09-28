from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = "shared-secret-with-car-api"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    host: str = "0.0.0.0"
    port: int = 8090


@lru_cache
def get_settings() -> Settings:
    return Settings()
