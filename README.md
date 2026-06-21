# jira-sync — one-way JIRA Cloud → JIRA Cloud mirror

A small FastAPI service that listens to JIRA Cloud webhooks from a **source** instance and mirrors the same actions (issue create, edit, status change, comments, attachments) into a **target** JIRA Cloud instance.

- **Direction:** one-way (source → target). The target is treated as a read-only mirror.
- **Trigger:** JIRA webhooks (real-time). No polling.
- **State:** a tiny SQLite file maps source issue/comment/attachment IDs to their target counterparts.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Create API tokens on both JIRAs](#2-create-api-tokens-on-both-jiras)
3. [Install the project locally](#3-install-the-project-locally)
4. [Configure `.env` and `config.yml`](#4-configure-env-and-configyml)
5. [Start the sync service](#5-start-the-sync-service)
6. [Expose the service with ngrok](#6-expose-the-service-with-ngrok)
7. [Register the webhook in JIRA A (source)](#7-register-the-webhook-in-jira-a-source)
8. [Verify it works](#8-verify-it-works)
9. [Troubleshooting](#9-troubleshooting)
10. [Limitations & next steps](#10-limitations--next-steps)

---

## 1. Prerequisites

You need:

| Requirement | Notes |
|---|---|
| Windows 10/11 | This guide uses PowerShell. macOS/Linux commands are similar. |
| `winget` | Built into Windows 11 — check with `winget --version`. |
| Two JIRA Cloud sites | Both must be `*.atlassian.net`. Self-hosted (Data Center) is **not** covered here. |
| Admin rights on JIRA A (source) | Needed to register a webhook. |
| Project-create rights on JIRA B (target) | The target project must already exist. |
| ngrok account (free tier is fine) | Used to give your local server a public HTTPS URL. Sign up at https://ngrok.com — installed in step 1b below. |

We install **Python** and **ngrok** in the next two sub-steps. Everything else (FastAPI, requests, etc.) is installed inside a virtual environment in step 3.

**Important — issue types and workflows.**
This service assumes the source and target projects share the **same issue types and the same status names** in their workflow (e.g. both have `Story`, `Task`, `Bug` and both move through `To Do → In Progress → Done`). If they differ, see [Limitations](#10-limitations--next-steps).

### 1a. Install Python 3.12

Windows ships with a placeholder `python.exe` that redirects to the Microsoft Store — it doesn't actually run anything. We need a real Python install.

**Option A — using winget (recommended):**

```powershell
winget install -e --id Python.Python.3.12
```

This installs Python to `C:\Users\<you>\AppData\Local\Programs\Python\Python312\` and adds it to your `PATH`.

After it finishes, **close and reopen your terminal** so the new `PATH` takes effect.

Then turn off the Microsoft Store alias so it doesn't shadow the real `python`:
1. Open **Settings → Apps → Advanced app settings → App execution aliases**.
2. Toggle **App Installer — python.exe** and **App Installer — python3.exe** to **Off**.

**Option B — manual installer:**
Download from https://www.python.org/downloads/ and during install tick **"Add python.exe to PATH"**.

**Verify:**

```powershell
py --version
# Should print: Python 3.12.x

pip --version
# Should print a pip version and a path inside Python312\
```

If `python --version` still prints the Microsoft Store message, your terminal is using the old `PATH` — close and reopen PowerShell, then try again.

### 1b. Install ngrok

```powershell
winget install -e --id Ngrok.Ngrok
```

Close and reopen your terminal, then verify:

```powershell
ngrok version
```

Sign up at https://dashboard.ngrok.com, copy your auth token from the dashboard, and register it (one-time):

```powershell
ngrok config add-authtoken <your-ngrok-token>
```

---

## 2. Create API tokens on both JIRAs

You need one token per JIRA site. The service authenticates as the user who owns the token, so anything that user can see/edit is what gets synced.

For **each** of the two JIRA sites:

1. Sign in to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **Create API token**.
3. Give it a label like `jira-sync-source` (or `jira-sync-target`).
4. **Copy the token immediately** — you cannot view it again.
5. Note the email address of the account you're signed in as. You will need `email + token` for Basic auth.

> Tip: create a dedicated bot user in each JIRA if you can. That makes it easy to filter the bot's own activity later and avoids attributing automated changes to a real person.

---

## 3. Install the project locally

Open PowerShell and run:

```powershell
cd C:\Users\shiva\jira-sync

# Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

If `Activate.ps1` is blocked by execution policy, run PowerShell once as Administrator:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

To confirm the install worked:

```powershell
python -c "import fastapi, uvicorn, requests, yaml, dotenv; print('ok')"
```

You should see `ok`.

---

## 4. Configure `.env` and `config.yml`

### 4a. Secrets — `.env`

Copy the example and edit it:

```powershell
copy .env.example .env
notepad .env
```

Fill in:

```env
SOURCE_BASE_URL=https://your-source.atlassian.net
SOURCE_EMAIL=source-user@example.com
SOURCE_TOKEN=<token from step 2 for source>

TARGET_BASE_URL=https://your-target.atlassian.net
TARGET_EMAIL=target-user@example.com
TARGET_TOKEN=<token from step 2 for target>
```

`*_BASE_URL` is just the host, no trailing slash, no `/rest/...` path.

### 4b. Project mapping — `config.yml`

```yaml
source_project: ABC      # project key on the source JIRA you want to mirror
target_project: XYZ      # project key on the target JIRA that will receive issues
sync_attachments: true   # set to false if you don't want files copied
db_path: data/mapping.sqlite
```

Use the **project key** (e.g. `ABC`), not the project name. You can find it in the URL when you open the project: `https://your-source.atlassian.net/jira/software/projects/ABC/board`.


---

## 5. Start the sync service

From the project directory with the venv activated:

```powershell
uvicorn main:app --port 8000 --reload
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Quick health check in a second terminal:

```powershell
curl.exe http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

Leave this terminal running. All sync activity is logged here.

---

## 6. Expose the service with ngrok

JIRA Cloud webhooks need a **public HTTPS URL**. ngrok gives you one pointing at your local port. (You already installed ngrok and added your auth token in step 1b.)

1. In a **second** terminal (keep uvicorn running in the first), start the tunnel:

   ```powershell
   ngrok http 8000
   ```

2. ngrok prints something like:

   ```
   Forwarding   https://1a2b-203-0-113-7.ngrok-free.app -> http://localhost:8000
   ```

   **Copy that `https://...ngrok-free.app` URL.** This is your webhook base.

> The free ngrok URL changes every time you restart it. If you stop and restart ngrok, you'll need to update the webhook URL in JIRA. A paid static domain avoids this.

---

## 7. Register the webhook in JIRA A (source)

1. As a JIRA admin on the **source** site, go to:
   **⚙ Settings → System → WebHooks** (URL: `https://<source>.atlassian.net/plugins/servlet/webhooks`).
2. Click **+ Create a WebHook**.
3. Fill in:

   | Field | Value |
   |---|---|
   | Name | `jira-sync` |
   | Status | Enabled |
   | URL | `https://<your-ngrok-id>.ngrok-free.app/webhook` |
   | Issue related events — JQL | `project = ABC` (replace with your source project key) |
   | Issue | ✅ created, ✅ updated |
   | Comment | ✅ created, ✅ updated |
   | Attachment | ✅ created |

4. Save.

The JQL filter is important — without it, your service will receive events from **every** project on JIRA A.

---

## 8. Verify it works

Open the uvicorn terminal so you can watch logs, then in JIRA A:

### Test 1 — Create

1. In source project `ABC`, create a new issue (any type, e.g. `Task`) titled `sync test 1`.
2. In the uvicorn log you should see:

   ```
   INFO ... received event: jira:issue_created
   INFO handlers - created ABC-123 -> XYZ-45
   ```

3. Open target project `XYZ` in your browser. The issue `sync test 1` should be there.

### Test 2 — Edit

1. On `ABC-123`, change the summary or description.
2. Log:

   ```
   INFO ... received event: jira:issue_updated
   INFO handlers - updated XYZ-45 fields: ['summary']
   ```

3. The change should be reflected on `XYZ-45`.

### Test 3 — Status transition

1. On `ABC-123`, move the status from `To Do` to `In Progress`.
2. Log:

   ```
   INFO handlers - transitioned XYZ-45 -> In Progress
   ```

3. `XYZ-45` should now be in `In Progress`.

### Test 4 — Comment

1. Add a comment on `ABC-123`.
2. Log:

   ```
   INFO ... received event: comment_created
   INFO handlers - comment 10001 -> 20002 on XYZ-45
   ```

3. Comment should appear on `XYZ-45`.

### Test 5 — Attachment

1. Drag a small file onto `ABC-123`.
2. Log:

   ```
   INFO ... received event: attachment_created
   INFO handlers - attachment 30001 -> 40002 on XYZ-45
   ```

3. File should appear under attachments on `XYZ-45`.

---

## 9. Troubleshooting

### `Application startup failed` — `KeyError: 'SOURCE_BASE_URL'`
Your `.env` is missing a key or wasn't saved. Re-check the file and make sure there are no quotes around the values.

### Webhook fires but uvicorn shows nothing
- Confirm the public URL in JIRA matches the current ngrok URL (it changes on restart).
- Open the ngrok inspector at http://127.0.0.1:4040 — it shows every inbound request and their bodies. If you see the request there but not in uvicorn, the path is wrong (must end in `/webhook`).

### `401 Unauthorized` from JIRA B
Bad email or token in `.env`. Run the `myself` curl from step 4c to confirm credentials.

### `400 Bad Request — Issue type 'Story' is not valid`
The target project doesn't have a matching issue type. Either add it on the target, or change the source issue to a type both projects share.

### `RuntimeError: no transition available on XYZ-45 to status 'In Review'`
The target workflow has no transition from its current status to `In Review`. Either align the workflows or extend `handlers.py` with a status mapping table.

### `Attachment failed` with `415 Unsupported Media Type`
The `X-Atlassian-Token: no-check` header is required and is already set in [jira_client.py](jira_client.py). If you customized that file, make sure the header is still there.

### Comments look like `[object Object]` on the target
You replaced the ADF body with a string somewhere. Pass the body through untouched — both v3 webhook payloads and v3 create/update APIs use ADF (Atlassian Document Format).

### Source and target are the same site
The current scaffold has no loop prevention. If you point both `SOURCE_*` and `TARGET_*` at the same site, the bot's own writes will trigger more webhooks → infinite loop. Don't do that.

---

## 10. Limitations & next steps

The PoC is intentionally small. Things it **does not** do today:

| Limitation | Why | How to extend |
|---|---|---|
| Assignee / reporter not synced | accountIds differ across tenants | Add a `email → accountId` map for the target; set `assignee: { accountId: ... }` in `update_issue`. |
| No backfill of existing issues | Webhook only fires on new events | Add a script that pages `/rest/api/3/search?jql=project=ABC` and replays each issue through the create handler. |
| No deletes or unlinks | Webhook for `issue_deleted` is not handled | Add a branch in `handle_event` that closes/archives the target issue. |
| No two-way sync | One-way by design | Add a webhook on the target, and tag the bot user so its own events are skipped to avoid loops. |
| Status mapping is by name | Workflows assumed identical | Add a `status_map: { "In Review": "Reviewing" }` to `config.yml` and consult it in `_on_issue_updated`. |
| Custom fields not synced | Customfield IDs differ across instances | Add a `custom_field_map` in `config.yml` and copy the values in `_on_issue_updated`. |
| Single process, no retries | PoC | Wrap target API calls with retry/backoff, or move events into a queue (e.g. Redis + RQ). |

---

## File layout

```
jira-sync/
├── .env.example       # template for secrets
├── .gitignore
├── config.py          # loads .env + config.yml into typed objects
├── config.yml         # project keys, sync toggles
├── data/              # SQLite mapping DB lives here
├── handlers.py        # one function per event type
├── jira_client.py     # REST v3 client (auth, issues, comments, attachments, transitions)
├── main.py            # FastAPI app, /webhook + /healthz
├── mapping.py         # SQLite store for source↔target ID mapping
├── README.md          # this file
└── requirements.txt
```
