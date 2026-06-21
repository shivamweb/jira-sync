# How jira-sync works (architecture & APIs)

This document explains the end-to-end flow of the service and the exact JIRA Cloud REST APIs used at each step. For installation and run instructions, see [README.md](README.md).

---

## High-level flow

```
   ┌──────────────────┐   webhook POST    ┌──────────────────┐   REST API call    ┌──────────────────┐
   │   JIRA A         │ ─────────────────▶│  Your service    │ ──────────────────▶│   JIRA B         │
   │  (source)        │   /webhook        │  (FastAPI, local)│  (Basic auth)      │  (target)        │
   └──────────────────┘                   └────────┬─────────┘                    └──────────────────┘
                                                   │
                                                   │  reads / writes
                                                   ▼
                                          ┌──────────────────┐
                                          │  mapping.sqlite  │
                                          │  source ↔ target │
                                          └──────────────────┘
```

1. Something happens on JIRA A (issue created, edited, comment added, file attached).
2. JIRA A fires a **webhook** — a POST request with a JSON payload to your ngrok URL.
3. ngrok forwards it to your local FastAPI app on `127.0.0.1:8000/webhook`.
4. The app inspects the `webhookEvent` field, looks up (or creates) the matching target ID in SQLite, then calls the **JIRA Cloud REST API v3** on JIRA B to perform the same action.
5. Result: JIRA B mirrors JIRA A in near-real-time.

---

## Authentication

Both sides use **HTTP Basic Auth** with `email + API token`. JIRA Cloud accepts this on every REST endpoint.

```http
Authorization: Basic base64(email:api_token)
```

In [jira_client.py](jira_client.py) this is set once on a `requests.Session` so every call carries it:

```python
self.session.auth = HTTPBasicAuth(email, token)
```

Webhooks coming **in** from JIRA A are not authenticated by default. JIRA fires them anonymously; the URL is the secret. (Optional improvement: add a shared secret header — JIRA supports signed webhooks.)

---

## Event-by-event walkthrough

JIRA's webhook event name → what the service does → which API it hits. All target-side endpoints are JIRA Cloud REST **v3**, documented at https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/.

### 1. Issue created — `jira:issue_created`

**Webhook payload contains:**

```json
{
  "webhookEvent": "jira:issue_created",
  "issue": {
    "key": "ABC-123",
    "fields": {
      "summary": "...",
      "description": { "...ADF..." },
      "issuetype": { "name": "Task" },
      "project": { "key": "ABC" }
    }
  }
}
```

**API called on JIRA B:**

```http
POST /rest/api/3/issue
```

**Handler:** `_on_issue_created` in [handlers.py](handlers.py) → `JiraClient.create_issue` in [jira_client.py](jira_client.py).

**Mapping write:** `issue_map(ABC-123 → XYZ-45)` so the service knows where to send future edits.

### 2. Issue updated — `jira:issue_updated`

**Webhook payload contains a `changelog`:**

```json
{
  "webhookEvent": "jira:issue_updated",
  "issue": { "key": "ABC-123", "fields": { "...current state..." } },
  "changelog": {
    "items": [
      { "field": "summary",  "toString": "new title" },
      { "field": "status",   "toString": "In Progress" }
    ]
  }
}
```

The changelog tells the service **what specifically changed** — only those fields are updated on the target.

**APIs called on JIRA B (depending on what changed):**

| Changelog field | API call |
|---|---|
| `summary`, `description`, `priority` | `PUT /rest/api/3/issue/{key}` |
| `status` | `GET /rest/api/3/issue/{key}/transitions` to find the matching transition ID, then `POST /rest/api/3/issue/{key}/transitions` |

Status is special — JIRA does not let you set status directly. You have to POST a **transition** (the workflow's allowed move). The service looks up the transition whose destination matches the new status name.

**Handler:** `_on_issue_updated` in [handlers.py](handlers.py) → `JiraClient.update_issue` and `JiraClient.transition_issue`.

### 3. Comment created — `comment_created`

**Webhook payload:**

```json
{
  "webhookEvent": "comment_created",
  "issue":   { "key": "ABC-123" },
  "comment": { "id": "10001", "body": { "...ADF..." } }
}
```

**API called on JIRA B:**

```http
POST /rest/api/3/issue/{targetKey}/comment
```

**Handler:** `_on_comment_created` in [handlers.py](handlers.py) → `JiraClient.add_comment`.
**Mapping write:** `comment_map(10001 → 20002)` so a future edit can find it.

### 4. Comment updated — `comment_updated`

Same payload shape, but the comment already exists on the target. The service looks up the target comment ID from the map and calls:

```http
PUT /rest/api/3/issue/{targetKey}/comment/{targetCommentId}
```

**Handler:** `_on_comment_updated` in [handlers.py](handlers.py).

### 5. Attachment created — `attachment_created`

Attachments are the trickiest because the file lives on JIRA A's storage. Two API calls per attachment:

**Step 1 — download from JIRA A** (authenticated GET against the URL JIRA included in the webhook payload):

```http
GET https://source.atlassian.net/rest/api/3/attachment/content/{id}
```

That returns the raw bytes.

**Step 2 — upload to JIRA B:**

```http
POST /rest/api/3/issue/{targetKey}/attachments
Content-Type: multipart/form-data
X-Atlassian-Token: no-check        # required, or JIRA rejects the upload
```

**Handler:** `_on_attachment_created` in [handlers.py](handlers.py) → `JiraClient.download_attachment` + `JiraClient.add_attachment`.

---

## Why the mapping DB exists

Issue keys (`ABC-123`) and IDs differ between the two instances. Without a persistent mapping, when JIRA A says *"comment added to ABC-123"*, the service wouldn't know which target issue (`XYZ-45`? `XYZ-99`?) it corresponds to.

SQLite ([mapping.py](mapping.py)) stores three tables:

| Table | Maps |
|---|---|
| `issue_map` | `ABC-123 → XYZ-45` |
| `comment_map` | source comment ID → target comment ID |
| `attachment_map` | source attachment ID → target attachment ID |

Every handler reads/writes this. It's the single source of truth that lets the service survive restarts.

---

## JIRA REST API endpoints summary

| Purpose | Method | Path |
|---|---|---|
| Auth sanity check | `GET` | `/rest/api/3/myself` |
| Create issue | `POST` | `/rest/api/3/issue` |
| Edit issue fields | `PUT` | `/rest/api/3/issue/{key}` |
| List available transitions | `GET` | `/rest/api/3/issue/{key}/transitions` |
| Apply a transition (status change) | `POST` | `/rest/api/3/issue/{key}/transitions` |
| Add comment | `POST` | `/rest/api/3/issue/{key}/comment` |
| Edit comment | `PUT` | `/rest/api/3/issue/{key}/comment/{id}` |
| Download attachment bytes | `GET` | `/rest/api/3/attachment/content/{id}` |
| Upload attachment | `POST` | `/rest/api/3/issue/{key}/attachments` |

All target-side calls are made by [jira_client.py](jira_client.py); all are dispatched from [handlers.py](handlers.py) based on the `webhookEvent` string.

---

## Atlassian Document Format (ADF) — one important detail

JIRA Cloud REST v3 uses **ADF** (a JSON tree) for `description` and `comment.body`, not plain strings. The good news: webhook payloads from JIRA A already give you the ADF — the service passes it through to JIRA B unchanged. That's why the code doesn't do any text conversion.

If the service ever switched to REST v2, descriptions would be plain wiki markup instead — but stick with v3.

---

## Request lifecycle — concrete example

Walking through a single "user adds a comment on ABC-123" event end-to-end:

1. A user on JIRA A clicks "Comment" on `ABC-123` and types "hello".
2. JIRA A's webhook engine fires:
   ```
   POST https://<ngrok>.ngrok-free.app/webhook
   { "webhookEvent": "comment_created", "issue": {"key": "ABC-123"}, "comment": {"id": "10001", "body": {...ADF...}} }
   ```
3. ngrok forwards the request to `127.0.0.1:8000/webhook`.
4. FastAPI's `webhook` handler in [main.py](main.py) reads the body and calls `handle_event(...)`.
5. `handle_event` dispatches on `webhookEvent == "comment_created"` → `_on_comment_created`.
6. The handler queries SQLite: `issue_map["ABC-123"]` → `"XYZ-45"`.
7. It calls `JiraClient.add_comment("XYZ-45", body_adf)` which executes:
   ```
   POST https://your-target.atlassian.net/rest/api/3/issue/XYZ-45/comment
   Authorization: Basic <base64(target_email:target_token)>
   Content-Type: application/json
   { "body": {...ADF...} }
   ```
8. JIRA B returns `201 Created` with the new comment's ID (e.g. `20002`).
9. The handler writes `comment_map["10001"] = "20002"` so future edits to comment `10001` will find their target.
10. The webhook handler returns `{"ok": true}` to JIRA A.

Total latency: typically 200–800ms, dominated by the round-trip to JIRA B.

---

## Component map

| File | Responsibility |
|---|---|
| [main.py](main.py) | FastAPI app. Defines `/webhook` and `/healthz`. Reads payloads, calls `handle_event`. |
| [config.py](config.py) | Loads `.env` + `config.yml` into a typed `Config` object. |
| [handlers.py](handlers.py) | One function per event type. Holds the dispatch logic and reads/writes the mapping DB. |
| [jira_client.py](jira_client.py) | Thin wrapper over `requests` for the JIRA REST v3 endpoints used. |
| [mapping.py](mapping.py) | SQLite store with three tables: issues, comments, attachments. |
| [config.yml](config.yml) | Non-secret config: project keys, attachment toggle, DB path. |
| `.env` | Secrets: base URLs, emails, tokens. Never committed. |

---

## Where to extend

- **Assignee sync** — extend `_on_issue_updated` to detect the `assignee` changelog field, look up the target `accountId` (via a config map keyed by email), and set `assignee: { accountId: ... }` in the update payload.
- **Custom fields** — extend `_on_issue_updated` with a `custom_field_map` from `config.yml` (e.g. `customfield_10001 → customfield_10042`).
- **Backfill** — add a one-shot script that pages `GET /rest/api/3/search?jql=project=ABC` and replays each issue through `_on_issue_created`.
- **Two-way sync** — add a second webhook registered on JIRA B and a "skip if author is the bot" filter to prevent loops.
- **Reliability** — wrap target API calls with retry/backoff (e.g. `tenacity`), or push events onto a queue (Redis + RQ) so retries survive process restarts.
