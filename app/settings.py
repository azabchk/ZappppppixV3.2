from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Configuration for the service."""
    # v2-style settings config (loads .env automatically if present)
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Use validation_alias to bind specific env var names
    database_url: str = Field(..., validation_alias="DATABASE_URL")
    admin_token: str = Field(..., validation_alias="ADMIN_TOKEN")
    default_instruments: str = Field("RUB,USD", validation_alias="DEFAULT_INSTRUMENTS")

    def instrument_list(self) -> List[str]:
        """Return the default instruments as a list of uppercase tickers."""
        return [i.strip().upper() for i in self.default_instruments.split(",") if i.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()