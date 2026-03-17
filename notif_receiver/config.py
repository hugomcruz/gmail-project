from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Google Cloud / Pub/Sub
    google_cloud_project_id: str = ""
    pubsub_subscription_name: str = "gmail-notifications-sub"
    pubsub_topic_name: str = "gmail-notifications"

    # Gmail OAuth2
    gmail_credentials_file: str = "credentials.json"
    gmail_token_file: str = "token.json"
    gmail_user_id: str = "me"

    # Raw JSON content of the OAuth2 client secret file.
    # Set this env var in serverless / container environments instead of
    # mounting client_secret.json as a file.  On first use the value is
    # written into the database so subsequent restarts don't need it.
    gmail_client_secret_json: str = ""

    # Redirect URI registered in Google Cloud Console for the OAuth web flow.
    # In production set this to the public URL of the callback endpoint.
    gmail_oauth_redirect_uri: str = "http://localhost:8000/gmail/auth/callback"

    # The email address to watch (defaults to authenticated user)
    gmail_watch_email: str = ""

    # Pub/Sub push webhook verification token (set this to a secret value)
    pubsub_verification_token: str = "change-me-secret-token"

    # App settings
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # Comma-separated Gmail label IDs to watch.
    # INBOX catches normal mail; add custom label IDs for archived/filtered emails.
    # Run GET /gmail/labels to list all available label IDs.
    # Example: INBOX,Label_123456789
    gmail_watched_labels: str = "INBOX"

    @property
    def watched_labels(self) -> list[str]:
        return [lbl.strip() for lbl in self.gmail_watched_labels.split(",") if lbl.strip()]

    # Set to True when running locally (no public URL available).
    # The app will pull messages from Pub/Sub instead of receiving pushes.
    use_pull_subscriber: bool = False

    # Set to False to disable the GET /health liveness endpoint.
    health_check_enabled: bool = True

    # Email processor — receives fully-fetched emails via HTTP POST
    email_processor_url: str = "http://localhost:8001"

    # PostgreSQL — used to persist the OAuth token across restarts
    database_url: str = "postgresql://emailprocessor:emailprocessor@localhost:5432/emailprocessor"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
