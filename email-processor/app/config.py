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

    # Outlook inbound (Microsoft Graph) device-code auth client ID.
    outlook_client_id: str = ""

    # Poll interval for pulling new Outlook messages.
    outlook_poll_interval_seconds: int = 60

    # Public HTTPS callback URL used by Microsoft Graph to deliver Outlook
    # message notifications. Example:
    # https://your-domain.example/api/inbound-webhooks/outlook
    outlook_webhook_notification_url: str = ""

    # Optional dedicated lifecycle callback URL. If omitted, notification URL is used.
    outlook_webhook_lifecycle_url: str = ""

    # Shared secret validated against Graph notification clientState.
    # If empty, a per-connection random state is generated and persisted.
    outlook_webhook_client_state_secret: str = ""

    # Background subscription renew cadence and safety window.
    outlook_webhook_renew_interval_seconds: int = 600
    outlook_webhook_renew_before_minutes: int = 30

    # URL of notif_receiver service used for Gmail auth/watch proxy calls.
    notif_receiver_url: str = "http://localhost:8000"

    # Set to False to disable the GET /health liveness endpoint.
    health_check_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
