from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = "shared-secret-with-car-api"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8090
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",") if part.strip()]
            return items or [
                "http://localhost:3000",
                "http://localhost:5173",
            ]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
