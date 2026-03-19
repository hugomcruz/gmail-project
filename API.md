# API Reference

This project exposes two backend services, each with their own HTTP API.

| Service | Default Port | Description |
|---|---|---|
| **notif-receiver** | 8000 | Receives Gmail change notifications via Google Cloud Pub/Sub and drives the OAuth/watch lifecycle |
| **email-processor** | 8001 | Rules engine, CRUD management API, OneDrive auth, user management |

The **rules-ui** (React SPA, port 3000) proxies `/api/` → email-processor and `/gmail/` → notif-receiver, so in production both APIs are reachable under a single public hostname.

---

## Authentication

All email-processor endpoints (except `POST /api/auth/login` and `GET /health`) require a **Bearer JWT** obtained from the login endpoint.

```
Authorization: Bearer <token>
```

User management endpoints additionally require the **`admin`** role.

The notif-receiver service has no user auth; the Pub/Sub push endpoint is verified via a shared secret query parameter.

---

## notif-receiver (port 8000)

### Pub/Sub

#### `POST /pubsub/push`

Receives push notifications from Google Cloud Pub/Sub. Called automatically by GCP; not intended for direct use.

**Query parameters**

| Name | Type | Required | Description |
|---|---|---|---|
| `token` | string | Yes | Must match `PUBSUB_VERIFICATION_TOKEN`; requests without it are rejected with 403 |

**Request body** — GCP Pub/Sub push envelope (JSON)

```json
{
  "message": {
    "data": "<base64-encoded JSON>",
    "messageId": "...",
    "publishTime": "..."
  },
  "subscription": "projects/my-project/subscriptions/my-sub"
}
```

The decoded `data` payload is a Gmail history notification: `{"emailAddress": "user@gmail.com", "historyId": "123456"}`.

**Response `200`**

```json
{ "status": "queued", "historyId": "123456" }
```

**Response `403`** — missing or wrong token  
**Response `400`** — malformed message body

---

### Gmail

All `/gmail/*` endpoints manage the Gmail OAuth token and Pub/Sub watch registration.

#### `POST /gmail/auth/start`

Starts the Google OAuth 2.0 web flow. Returns a URL the user must open in their browser.

**Response `200`**

```json
{ "auth_url": "https://accounts.google.com/o/oauth2/auth?..." }
```

---

#### `GET /gmail/auth/callback`

OAuth redirect URI. Google redirects here after the user grants consent. Exchanges the code for tokens and saves them. This endpoint is hidden from the OpenAPI schema.

**Query parameters** — `code`, `state` (populated automatically by Google)

**Response** — Redirects to `/` on success.

---

#### `GET /gmail/auth/status`

Returns the current OAuth flow state and token health. Poll this after starting the auth flow.

**Response `200`**

```json
{
  "flow_status": "idle | pending | success | error",
  "flow_message": "Human-readable status or null",
  "token_status": "valid | expired | missing | invalid | error"
}
```

| `flow_status` | Meaning |
|---|---|
| `idle` | No active flow |
| `pending` | Flow started, waiting for user |
| `success` | Flow completed successfully |
| `error` | Flow failed; see `flow_message` |

| `token_status` | Meaning |
|---|---|
| `valid` | Token file present and not expired |
| `expired` | Token exists but is expired |
| `missing` | No token file found |
| `invalid` | Token file exists but cannot be parsed |
| `error` | Unexpected error reading the token |

---

#### `DELETE /gmail/auth/status`

Resets the in-memory OAuth flow status back to `idle`. Use this to dismiss an error state before starting a new flow.

**Response `200`**

```json
{ "ok": true }
```

---

#### `POST /gmail/watch`

Registers (or renews) a Gmail Pub/Sub watch for the authenticated account. The watch tells Gmail to publish notifications to the configured Pub/Sub topic whenever the inbox changes.

**Response `200`** — Watch details returned by the Gmail API

```json
{
  "historyId": "123456",
  "expiration": "1700000000000"
}
```

**Response `401`** — No valid token; authenticate first  
**Response `500`** — Gmail API error

---

#### `DELETE /gmail/watch`

Stops the active Gmail Pub/Sub watch.

**Response `200`**

```json
{ "stopped": true }
```

---

#### `GET /gmail/labels`

Lists all labels in the authenticated Gmail account.

**Response `200`**

```json
[
  { "id": "INBOX", "name": "INBOX" },
  { "id": "Label_123", "name": "My Label" }
]
```

---

## email-processor (port 8001)

### Auth

#### `POST /api/auth/login`

Authenticate with username + password and receive a JWT access token.

**Request body**

```json
{
  "username": "admin",
  "password": "changeme"
}
```

**Response `200`**

```json
{
  "access_token": "<JWT>",
  "user": {
    "id": 1,
    "username": "admin",
    "role": "admin",
    "is_active": true
  }
}
```

**Response `401`** — Invalid credentials  
**Response `403`** — Account is disabled

---

#### `GET /api/auth/me`

Returns the currently authenticated user. Requires a valid Bearer token.

**Response `200`**

```json
{
  "id": 1,
  "username": "admin",
  "role": "admin",
  "is_active": true
}
```

---

### Rules

All rules endpoints require authentication.

#### `GET /api/rules`

List all rules.

**Response `200`** — Array of rule objects

```json
[
  {
    "id": 1,
    "name": "Archive invoices",
    "enabled": true,
    "match": "all",
    "conditions": [
      { "type": "subject_contains", "value": "invoice" }
    ],
    "actions": [
      { "type": "upload_to_onedrive", "connection": "my-onedrive", "folder": "Invoices" }
    ]
  }
]
```

---

#### `POST /api/rules`

Create a new rule.

**Request body**

```json
{
  "name": "Archive invoices",
  "enabled": true,
  "match": "all",
  "conditions": [
    { "type": "subject_contains", "value": "invoice" }
  ],
  "actions": [
    { "type": "upload_to_onedrive", "connection": "my-onedrive", "folder": "Invoices" }
  ]
}
```

`match` is `"all"` (all conditions must match) or `"any"` (at least one).

**Response `201`** — Created rule object  
**Response `422`** — Validation error

---

#### `GET /api/rules/{rule_id}`

Get a single rule by ID.

**Response `200`** — Rule object  
**Response `404`** — Not found

---

#### `PUT /api/rules/{rule_id}`

Update an existing rule. Accepts the same body as `POST /api/rules`.

**Response `200`** — Updated rule object  
**Response `404`** — Not found

---

#### `DELETE /api/rules/{rule_id}`

Delete a rule.

**Response `204`** — No content  
**Response `404`** — Not found

---

#### `POST /api/rules/{rule_id}/toggle`

Toggle a rule's `enabled` flag without sending a full update payload.

**Response `200`** — Updated rule object with the new `enabled` value

---

#### `POST /api/rules/reload`

Hot-reload all rules from the database into the running engine without restarting the service.

**Response `200`**

```json
{
  "status": "ok",
  "rules_loaded": 3,
  "message": "Reloaded 3 rule(s) from database"
}
```

---

#### `POST /api/rules/import-yaml`

Import rules from a `rules.yaml` file on the server into the database. Only imports if the database is currently empty (idempotent bootstrap).

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `rules_file` | string | `"rules.yaml"` | Path to YAML file on the server |

**Response `200`**

```json
{ "imported": 5, "message": "Imported 5 rule(s) from rules.yaml" }
```

**Response `404`** — File not found

---

### Connections

Connections are named, typed credential stores referenced by rule actions. All endpoints require authentication.

#### `GET /api/connections`

List all connections (token caches and sensitive fields are included in `fields`).

**Response `200`**

```json
[
  { "id": "my-onedrive", "type": "onedrive", "fields": {} },
  { "id": "my-s3", "type": "s3", "fields": { "bucket": "my-bucket" } }
]
```

---

#### `POST /api/connections`

Create a new connection.

**Request body**

```json
{
  "id": "my-onedrive",
  "type": "onedrive",
  "fields": {}
}
```

Supported types: `onedrive`, `s3`, `jira`, `mailgun`.

Required fields per type:

| Type | Required `fields` keys |
|---|---|
| `onedrive` | _(none — client_id is server-side config)_ |
| `s3` | `bucket` |
| `jira` | `url`, `user`, `token` |
| `mailgun` | `api_key`, `domain`, `sender_address` |

**Response `201`** — Created connection  
**Response `409`** — Connection ID already exists

---

#### `PUT /api/connections/{conn_id}`

Update an existing connection. Accepts the same body as `POST /api/connections`.

**Response `200`** — Updated connection  
**Response `404`** — Not found

---

#### `DELETE /api/connections/{conn_id}`

Delete a connection.

**Response `204`** — No content  
**Response `404`** — Not found

---

### Metadata

Metadata endpoints power the rule editor UI dropdowns. All require authentication.

#### `GET /api/meta/condition-types`

Returns the list of available condition types.

**Response `200`**

```json
[
  { "value": "from_equals",          "label": "From equals" },
  { "value": "from_contains",        "label": "From contains" },
  { "value": "to_contains",          "label": "To contains" },
  { "value": "subject_equals",       "label": "Subject equals" },
  { "value": "subject_contains",     "label": "Subject contains" },
  { "value": "subject_starts_with",  "label": "Subject starts with" },
  { "value": "subject_ends_with",    "label": "Subject ends with" },
  { "value": "body_contains",        "label": "Body contains" },
  { "value": "has_attachments",      "label": "Has attachments" },
  { "value": "attachment_count_gte", "label": "Attachment count ≥" },
  { "value": "label_contains",       "label": "Label contains" }
]
```

`has_attachments` takes no `value` field.

---

#### `GET /api/meta/action-types`

Returns the list of available action types.

**Response `200`**

```json
[
  { "value": "upload_to_s3",        "label": "Upload to S3" },
  { "value": "upload_to_onedrive",  "label": "Upload to OneDrive" },
  { "value": "create_jira_task",    "label": "Create JIRA task" },
  { "value": "forward_email",       "label": "Forward email" }
]
```

---

#### `GET /api/meta/connections`

Returns a simplified list of all connections for rule editor dropdowns.

**Response `200`**

```json
[
  { "id": "my-onedrive", "type": "onedrive", "label": "my-onedrive (onedrive)" }
]
```

---

### Action Logs

All endpoints require authentication.

#### `GET /api/logs`

List action log entries, newest first.

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `skip` | integer | 0 | Offset for pagination |
| `limit` | integer | 100 | Max records to return |
| `rule_name` | string | — | Filter by rule name (exact match) |
| `status` | string | — | Filter by status (`success`, `error`, etc.) |

**Response `200`** — Array of log entries

```json
[
  {
    "id": 42,
    "rule_name": "Archive invoices",
    "email_id": "18abc123",
    "email_subject": "Invoice #1001",
    "action_type": "upload_to_onedrive",
    "status": "success",
    "detail": null,
    "created_at": "2024-01-15T10:00:00Z"
  }
]
```

---

#### `GET /api/logs/count`

Count log entries, with optional filters.

**Query parameters** — same as `GET /api/logs` (`rule_name`, `status`)

**Response `200`**

```json
{ "count": 157 }
```

---

### OneDrive Auth

Device code flow for authenticating OneDrive connections. No user auth required (connection ID is the identifier).

#### `POST /api/onedrive-auth/{conn_id}/start`

Initiate a Microsoft device code flow for the given connection. Returns a user code and URL that the user must open in their browser. Simultaneously spawns a background thread that waits for completion and saves the token to the database.

If a valid token already exists for the connection, responds immediately with `status: "success"` instead of starting a new flow.

**Path parameters**

| Name | Description |
|---|---|
| `conn_id` | ID of the OneDrive connection (must exist in DB) |

**Request body** (optional)

```json
{
  "client_id": ""
}
```

`client_id` resolution order: body → connection's stored `client_id` field → `ONEDRIVE_CLIENT_ID` env var.

**Response `200`** — Pending flow

```json
{
  "status": "pending",
  "user_code": "ABCD-1234",
  "verification_url": "https://microsoft.com/devicelogin",
  "message": "Go to https://microsoft.com/devicelogin and enter code ABCD-1234",
  "expires_at": "2024-01-15T10:15:00+00:00"
}
```

**Response `200`** — Already authenticated

```json
{
  "status": "success",
  "message": "Already authenticated (token is valid)."
}
```

**Response `400`** — No `client_id` configured  
**Response `500`** — Microsoft device flow initiation failed

---

#### `GET /api/onedrive-auth/{conn_id}/status`

Poll the current authentication status. Call this repeatedly (e.g. every 3 seconds) after `start` until `status` is no longer `"pending"`.

**Response `200`**

```json
{ "status": "idle | pending | success | error", "message": "..." }
```

| `status` | Meaning |
|---|---|
| `idle` | No active or recent flow |
| `pending` | Waiting for user to enter the device code |
| `success` | Token acquired and saved to DB |
| `error` | Auth failed; see `message` |

---

#### `DELETE /api/onedrive-auth/{conn_id}/status`

Clear the in-memory auth state so a new flow can be started.

**Response `200`**

```json
{ "cleared": true }
```

---

### Users

User management requires the `admin` role.

#### `GET /api/users`

List all users.

**Response `200`**

```json
[
  { "id": 1, "username": "admin", "role": "admin", "is_active": true }
]
```

---

#### `POST /api/users`

Create a new user.

**Request body**

```json
{
  "username": "alice",
  "password": "securepassword",
  "role": "user",
  "is_active": true
}
```

`role` is `"admin"` or `"user"`.

**Response `201`** — Created user object  
**Response `409`** — Username already exists

---

#### `PUT /api/users/{user_id}`

Update an existing user. All fields are optional.

**Request body**

```json
{
  "username": "alice",
  "password": "newpassword",
  "role": "admin",
  "is_active": true
}
```

**Response `200`** — Updated user object  
**Response `404`** — Not found  
**Response `409`** — Username conflict

---

#### `DELETE /api/users/{user_id}`

Delete a user. Admins cannot delete their own account.

**Response `204`** — No content  
**Response `400`** — Cannot delete own account  
**Response `404`** — Not found

---

### Internal Endpoints

These are not included in the OpenAPI schema and are intended for inter-service or debugging use only.

#### `POST /internal/process-email`

Called by the notif-receiver service with a fully-fetched email dict. Runs the email through the rules engine and returns a per-rule summary.

**Request body** — Email object

```json
{
  "id": "18abc123",
  "subject": "Invoice #1001",
  "from": "billing@example.com",
  "to": "me@example.com",
  "body": "...",
  "attachments": []
}
```

**Response `200`**

```json
[
  {
    "rule": "Archive invoices",
    "matched": true,
    "actions": [
      { "action": "upload_to_onedrive", "status": "success" }
    ]
  }
]
```

---

#### `GET /internal/engine-status`

Returns the current state of the in-memory rules engine and connection registry. Useful for debugging.

**Response `200`**

```json
{
  "rules_loaded": 3,
  "db_mode": true,
  "rules": [ ... ],
  "connections": ["my-onedrive", "my-s3"]
}
```

---

### Health

#### `GET /health`

Returns service health. Only available when `HEALTH_CHECK_ENABLED=true` (default: true).

**Response `200`**

```json
{
  "status": "ok",
  "rules_loaded": 3,
  "db_mode": true
}
```
