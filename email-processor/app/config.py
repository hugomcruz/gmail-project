from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://emailprocessor:emailprocessor@localhost:5432/emailprocessor"

    # App
    log_level: str = "INFO"

    # Auth
    jwt_secret_key: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 hours

    # OneDrive — Azure app Client ID used for MSAL device-code auth.
    # All OneDrive connections share this client ID.
    onedrive_client_id: str = ""

    # Set to False to disable the GET /health liveness endpoint.
    health_check_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
