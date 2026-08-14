from functools import lru_cache
from typing import Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: str = ""
    owner_ids: Annotated[tuple[int, ...], NoDecode] = ()
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    timezone: str = "Asia/Tehran"
    rebecca_base_url: str = ""
    rebecca_bearer_token: str = ""
    rebecca_admin_api_mode: str = "auto"
    dry_run: bool = True
    destructive_actions: bool = False
    allow_disable_actions: bool = False
    allow_delete_actions: bool = False
    sync_interval_seconds: int = 300
    user_delete_grace_hours: int = 72
    trial_duration_hours: int = 24
    trial_traffic_gb: int = 10
    time_warning_thresholds: Annotated[tuple[int, ...], NoDecode] = (7, 3, 1)
    traffic_warning_thresholds: Annotated[tuple[int, ...], NoDecode] = (20, 10, 5)
    plisio_enabled: bool = False
    plisio_secret_key: str = ""
    plisio_source_currency: str = "USD"
    public_base_url: str = ""
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8080

    @field_validator("owner_ids", mode="before")
    @classmethod
    def parse_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(x.strip()) for x in value.split(",") if x.strip())
        return value

    @field_validator("time_warning_thresholds", "traffic_warning_thresholds", mode="before")
    @classmethod
    def parse_thresholds(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(x.strip()) for x in value.split(",") if x.strip())
        return value

@lru_cache
def get_settings() -> Settings:
    return Settings()
