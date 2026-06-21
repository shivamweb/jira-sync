import logging
import re
import threading

from jira_client import JiraClient
from mapping import Store

log = logging.getLogger("handlers")

_MENTION_RE = re.compile(r"\[~accountid:([^\]]+)\]")
_user_name_cache: dict[str, str] = {}

_issue_locks: dict[str, threading.Lock] = {}
_issue_locks_master = threading.Lock()


def _get_issue_lock(src_key: str) -> threading.Lock:
    with _issue_locks_master:
        lock = _issue_locks.get(src_key)
        if lock is None:
            lock = threading.Lock()
            _issue_locks[src_key] = lock
        return lock


def _resolve_mentions(body, src: JiraClient):
    if not isinstance(body, str):
        return body

    def repl(m):
        acct = m.group(1)
        if acct not in _user_name_cache:
            _user_name_cache[acct] = src.get_user_display_name(acct) or acct
        return f"@{_user_name_cache[acct]}"

    return _MENTION_RE.sub(repl, body)


def _to_adf(body):
    if body is None or isinstance(body, dict):
        return body
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": str(body)}]}
        ],
    }

_store: Store | None = None
_source: JiraClient | None = None
_target: JiraClient | None = None


def _init(cfg):
    global _store, _source, _target
    if _store is None:
        _store = Store(cfg.db_path)
        _source = JiraClient(cfg.source.base_url, cfg.source.email, cfg.source.token)
        _target = JiraClient(cfg.target.base_url, cfg.target.email, cfg.target.token)
    return _store, _source, _target


def handle_event(event: str, payload: dict, cfg):
    store, src, tgt = _init(cfg)

    issue = payload.get("issue") or {}
    src_project = (issue.get("fields", {}).get("project") or {}).get("key")
    if src_project and src_project != cfg.source.project_key:
        log.info("ignoring event for project %s (watching %s)", src_project, cfg.source.project_key)
        return

    src_key = issue.get("key")
    if src_key:
        with _get_issue_lock(src_key):
            _dispatch(event, payload, store, src, tgt, cfg)
    else:
        _dispatch(event, payload, store, src, tgt, cfg)


def _dispatch(event, payload, store, src, tgt, cfg):
    if event == "jira:issue_created":
        _on_issue_created(payload, store, src, tgt, cfg)
    elif event == "jira:issue_updated":
        _on_issue_updated(payload, store, src, tgt, cfg)
    elif event == "comment_created":
        _on_comment_created(payload, store, src, tgt, cfg)
    elif event == "comment_updated":
        _on_comment_updated(payload, store, src, tgt, cfg)
    elif event == "attachment_created" and cfg.sync_attachments:
        _on_attachment_created(payload, store, src, tgt, cfg)
    else:
        log.info("ignoring event %s", event)


def _on_issue_created(payload, store, src, tgt, cfg):
    issue = payload["issue"]
    src_key = issue["key"]
    if store.get_issue(src_key):
        log.info("issue %s already mapped, skipping create", src_key)
        return
    fields = issue["fields"]
    target_key = tgt.create_issue(
        project_key=cfg.target.project_key,
        summary=fields.get("summary", ""),
        description_adf=_to_adf(_resolve_mentions(fields.get("description"), src)),
        issue_type=fields["issuetype"]["name"],
    )
    store.put_issue(src_key, target_key)
    log.info("created %s -> %s", src_key, target_key)


def _on_issue_updated(payload, store, src, tgt, cfg):
    issue = payload["issue"]
    src_key = issue["key"]
    target_key = store.get_issue(src_key)
    if not target_key:
        log.warning("update for unmapped issue %s; backfilling create", src_key)
        _on_issue_created(payload, store, src, tgt, cfg)
        target_key = store.get_issue(src_key)
        if not target_key:
            return

    changelog = payload.get("changelog", {})
    fields_to_update: dict = {}
    status_change = None

    for item in changelog.get("items", []):
        f = item.get("field")
        if f == "summary":
            fields_to_update["summary"] = item.get("toString") or issue["fields"].get("summary")
        elif f == "description":
            desc = issue["fields"].get("description")
            if desc is None:
                continue
            fields_to_update["description"] = _to_adf(_resolve_mentions(desc, src))
        elif f == "priority" and item.get("toString"):
            fields_to_update["priority"] = {"name": item["toString"]}
        elif f == "status" and item.get("toString"):
            status_change = item["toString"]
        elif f == "Attachment" and cfg.sync_attachments and item.get("to"):
            _sync_attachment(item["to"], item.get("toString"), store, src, tgt, target_key)

    if fields_to_update:
        tgt.update_issue(target_key, fields_to_update)
        log.info("updated %s fields: %s", target_key, list(fields_to_update))
    if status_change:
        tgt.transition_issue(target_key, status_change)
        log.info("transitioned %s -> %s", target_key, status_change)


def _on_comment_created(payload, store, src, tgt, cfg):
    issue = payload["issue"]
    comment = payload["comment"]
    src_comment_id = str(comment["id"])
    if store.get_comment(src_comment_id):
        return
    target_key = store.get_issue(issue["key"])
    if not target_key:
        log.warning("comment on unmapped issue %s; skipping", issue["key"])
        return
    target_id = tgt.add_comment(target_key, _to_adf(_resolve_mentions(comment["body"], src)))
    store.put_comment(src_comment_id, str(target_id))
    log.info("comment %s -> %s on %s", src_comment_id, target_id, target_key)


def _on_comment_updated(payload, store, src, tgt, cfg):
    issue = payload["issue"]
    comment = payload["comment"]
    src_comment_id = str(comment["id"])
    target_id = store.get_comment(src_comment_id)
    target_key = store.get_issue(issue["key"])
    if not target_id or not target_key:
        return
    tgt.update_comment(target_key, target_id, _to_adf(_resolve_mentions(comment["body"], src)))
    log.info("updated comment %s on %s", target_id, target_key)


def _sync_attachment(src_attachment_id, filename_hint, store, src, tgt, target_key):
    src_id = str(src_attachment_id)
    if store.get_attachment(src_id):
        return
    meta = src.get_attachment_meta(src_id)
    if not meta or not meta.get("content"):
        log.warning("could not fetch attachment %s metadata; skipping", src_id)
        return
    content = src.download_attachment(meta["content"])
    target_id = tgt.add_attachment(
        target_key,
        meta.get("filename") or filename_hint or "file",
        content,
        meta.get("mimeType", "application/octet-stream"),
    )
    store.put_attachment(src_id, str(target_id))
    log.info("attachment %s -> %s on %s", src_id, target_id, target_key)


def _on_attachment_created(payload, store, src, tgt, cfg):
    # Jira Cloud's attachment_created webhook does not include the parent issue,
    # so we cannot route the attachment here. Attachments are synced via the
    # Attachment changelog item in jira:issue_updated instead.
    if "issue" not in payload:
        log.info("attachment_created without issue context; handled via issue_updated")
        return
    issue = payload["issue"]
    attachment = payload["attachment"]
    src_id = str(attachment["id"])
    if store.get_attachment(src_id):
        return
    target_key = store.get_issue(issue["key"])
    if not target_key:
        log.warning("attachment on unmapped issue %s; skipping", issue["key"])
        return
    content = src.download_attachment(attachment["content"])
    target_id = tgt.add_attachment(
        target_key,
        attachment["filename"],
        content,
        attachment.get("mimeType", "application/octet-stream"),
    )
    store.put_attachment(src_id, str(target_id))
    log.info("attachment %s -> %s on %s", src_id, target_id, target_key)
