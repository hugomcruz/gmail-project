# Gmail Pub/Sub Processor

A **FastAPI** service that receives Gmail change notifications delivered via
**Google Cloud Pub/Sub** push subscriptions and processes them in real time.

---

## Architecture

```
Gmail ──watch──▶ Pub/Sub Topic ──push──▶ POST /pubsub/push?token=…
                                              │
                                    FastAPI processes the message
                                    (fetch history, inspect emails, …)
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | Tested on 3.11 / 3.12 |
| Google Cloud project | With billing enabled |
| Gmail API enabled | GCP Console → APIs & Services |
| Pub/Sub API enabled | GCP Console → APIs & Services |
| OAuth 2.0 credentials | Desktop app type; download `credentials.json` |
| Public HTTPS endpoint | Required for Pub/Sub push (use [ngrok](https://ngrok.com) locally) |

---

## Quick Start

### 1. Clone & install

```bash
git clone <repo-url> gmail-processor
cd gmail-processor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your GCP project ID, topic name, and verification token
```

Generate a secure verification token:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3. Obtain OAuth2 credentials

1. Go to **GCP Console → APIs & Services → Credentials**.
2. Create an **OAuth 2.0 Client ID** (Desktop app).
3. Download the JSON file and save it as `credentials.json` in the project root.

Run the app once to trigger the browser-based OAuth flow:

```bash
python -m app.main
```

A `token.json` file will be created and reused on subsequent runs.

### 4. Set up Google Cloud Pub/Sub

```bash
PROJECT_ID=your-gcp-project-id
TOPIC=gmail-notifications
SUB=gmail-notifications-sub
PUSH_URL="https://your-domain.com/pubsub/push?token=<PUBSUB_VERIFICATION_TOKEN>"

# Create topic
gcloud pubsub topics create $TOPIC --project=$PROJECT_ID

# Grant Gmail permission to publish to the topic
gcloud pubsub topics add-iam-policy-binding $TOPIC \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project=$PROJECT_ID

# Create push subscription
gcloud pubsub subscriptions create $SUB \
  --topic=$TOPIC \
  --push-endpoint="$PUSH_URL" \
  --ack-deadline=60 \
  --project=$PROJECT_ID
```

### 5. Start Gmail watch

```bash
curl -X POST http://localhost:8000/gmail/watch
```

> The watch expires after ~7 days. Schedule this call with **Cloud Scheduler**
> or a cron job to renew it automatically.

### 6. Run the server

```bash
uvicorn app.main:app --reload
```

Or:

```bash
python -m app.main
```

Interactive docs are available at `http://localhost:8000/docs`.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | Liveness probe |
| `POST` | `/pubsub/push?token=…` | Pub/Sub push webhook |
| `POST` | `/gmail/watch` | Start Gmail push notifications |
| `DELETE` | `/gmail/watch` | Stop Gmail push notifications |

---

## Local development with ngrok

Pub/Sub push requires a publicly reachable HTTPS URL. Use **ngrok** during
development:

```bash
ngrok http 8000
```

Copy the `https://…ngrok-free.app` URL and update the Pub/Sub push subscription:

```bash
gcloud pubsub subscriptions modify-push-config gmail-notifications-sub \
  --push-endpoint="https://<ngrok-id>.ngrok-free.app/pubsub/push?token=<TOKEN>" \
  --project=$PROJECT_ID
```

---

## Project Structure

```
gmail-processor/
├── app/
│   ├── main.py                  # FastAPI app & entry point
│   ├── config.py                # Pydantic settings (reads .env)
│   ├── models.py                # Pydantic request/response models
│   ├── routers/
│   │   ├── pubsub.py            # POST /pubsub/push webhook
│   │   └── gmail.py             # POST/DELETE /gmail/watch
│   └── services/
│       ├── gmail_service.py     # Gmail API wrapper
│       └── pubsub_service.py    # Token verification & message parsing
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Extending the processor

Open [app/services/pubsub_service.py](app/services/pubsub_service.py) and add
your business logic inside `process_notification()`:

```python
def process_notification(notification: GmailNotification) -> dict:
    ...
    for record in history_records:
        for msg_added in record.get("messagesAdded", []):
            message = get_message(msg_added["message"]["id"])
            # ✏️  Add your custom logic here:
            #   - Store in a database
            #   - Forward to Slack / Teams
            #   - Trigger a workflow
            ...
```
