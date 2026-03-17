"""
One-time setup script: creates the Pub/Sub topic and grants the Gmail API
push service account the Publisher role.

Usage:
    python setup_pubsub.py
"""

import os
import sys
from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from app.config import get_settings

GMAIL_PUSH_SA = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
SETUP_TOKEN_FILE = "token_setup.json"
SETUP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def get_setup_credentials() -> Credentials:
    """Get credentials with cloud-platform scope (separate from the Gmail token)."""
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


def main() -> None:
    settings = get_settings()
    project_id = settings.google_cloud_project_id
    topic_name = settings.pubsub_topic_name
    topic_path = f"projects/{project_id}/topics/{topic_name}"

    print("Authenticating with cloud-platform scope …")
    creds = get_setup_credentials()
    publisher = pubsub_v1.PublisherClient(credentials=creds)

    # 1. Create topic
    print(f"Creating topic: {topic_path}")
    try:
        publisher.create_topic(request={"name": topic_path})
        print("  ✓ Topic created.")
    except AlreadyExists:
        print("  ✓ Topic already exists.")

    # 2. Set IAM policy — grant gmail-api-push publisher access
    print(f"Setting IAM policy on topic …")
    try:
        policy = publisher.get_iam_policy(request={"resource": topic_path})
        binding = next(
            (b for b in policy.bindings if b.role == "roles/pubsub.publisher"),
            None,
        )
        if binding is None:
            from google.iam.v1 import policy_pb2
            policy.bindings.add(
                role="roles/pubsub.publisher",
                members=[GMAIL_PUSH_SA],
            )
        elif GMAIL_PUSH_SA not in binding.members:
            binding.members.append(GMAIL_PUSH_SA)
        else:
            print("  ✓ IAM binding already exists.")

        publisher.set_iam_policy(request={"resource": topic_path, "policy": policy})
        print(f"  ✓ Granted roles/pubsub.publisher to {GMAIL_PUSH_SA}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ Could not set IAM policy: {exc}", file=sys.stderr)
        print("  → Set it manually in GCP Console: Pub/Sub → Topics → gmail-notifications → Permissions", file=sys.stderr)

    # 3. Create pull subscription (for local development — no public URL needed)
    sub_path = f"projects/{project_id}/subscriptions/{settings.pubsub_subscription_name}"
    print(f"Creating pull subscription: {sub_path}")
    subscriber = pubsub_v1.SubscriberClient(credentials=creds)
    with subscriber:
        try:
            subscriber.create_subscription(
                request={"name": sub_path, "topic": topic_path, "ack_deadline_seconds": 60}
            )
            print("  ✓ Pull subscription created.")
        except AlreadyExists:
            print("  ✓ Pull subscription already exists.")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ Could not create subscription: {exc}", file=sys.stderr)

    print("\nSetup complete! Add USE_PULL_SUBSCRIBER=true to your .env to run locally.")


if __name__ == "__main__":
    main()
