"""
Switch the Pub/Sub subscription to push mode pointing at your tunnel URL.

Usage:
    python configure_push.py https://your-tunnel-domain.com

What it does:
1. Takes the public HTTPS base URL as a CLI argument.
2. Calls modify_push_config on the existing Pub/Sub subscription so that
   Google Cloud delivers messages to:
       https://your-tunnel-domain.com/pubsub/push?token=<VERIFICATION_TOKEN>
3. Prints the configured push endpoint.
"""

import os
import sys

from google.cloud import pubsub_v1
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

from notif_receiver.config import get_settings

SETUP_TOKEN_FILE = "token_setup.json"
SETUP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_setup_credentials() -> Credentials:
    """Return GCP credentials with cloud-platform scope."""
    settings = get_settings()
    creds = None

    if os.path.exists(SETUP_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(SETUP_TOKEN_FILE, SETUP_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                settings.gmail_credentials_file, SETUP_SCOPES
            )
            creds = flow.run_local_server(port=8888)
        with open(SETUP_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    settings = get_settings()
    project_id = settings.google_cloud_project_id
    sub_name = settings.pubsub_subscription_name
    sub_path = f"projects/{project_id}/subscriptions/{sub_name}"
    verification_token = settings.pubsub_verification_token

    # 1. Public base URL passed as CLI argument
    if len(sys.argv) < 2:
        print("Usage: python configure_push.py https://your-tunnel-domain.com", file=sys.stderr)
        sys.exit(1)
    public_url = sys.argv[1].rstrip("/")
    push_endpoint = f"{public_url}/pubsub/push?token={verification_token}"

    # 2. Authenticate
    print("Authenticating with GCP …")
    creds = get_setup_credentials()

    # 3. Update subscription push config
    print(f"Updating subscription '{sub_path}' …")
    subscriber = pubsub_v1.SubscriberClient(credentials=creds)
    with subscriber:
        subscriber.modify_push_config(
            request={
                "subscription": sub_path,
                "push_config": {"push_endpoint": push_endpoint},
            }
        )

    print(
        "\n✓ Push subscription configured!\n"
        f"  Endpoint : {push_endpoint}\n\n"
        "Google Cloud Pub/Sub will now POST Gmail notifications to that URL."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"\n✗ Error: {exc}", file=sys.stderr)
        sys.exit(1)
