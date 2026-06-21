# Jira-Sync Test Checklist

- [Passed] Create new task on source → appears on target
- [Passed] Edit description → reflected on target
- [Passed] `[~accountid:...]` in description resolves to display name
- [Passed] Add comment → appears on target
- [Passed] Edit comment → updated on target
- [Passed] Delete comment on source → NOT deleted on target (intentional)
- [Passed] Inline attachments in description (broken media references)
- [Passed] Standalone attachment (drag file onto issue, not into description) — now routed via `jira:issue_updated` Attachment changelog
- [Passed] Summary-only edit
- [Passed] Priority change (may 400 if target screen omits Priority)
- [Passed] Status transitions in both directions (To Do → In Progress → Done → To Do)
- [Passed] Out-of-project event ignored (event from a project the config isn't watching)
- [Passed] Mention inside a comment resolves to display name
- [Passed] Long description (≥ a few KB)
- [Passed] Special characters: emoji, `<>&`, non-ASCII names
- [Passed] Restarting uvicorn keeps SQLite mappings intact (`mapping.py` Store persists)

- [Failed] Issue type change (Task → Bug)
- [Failed] Comment ordering under rapid posts (race in concurrent webhook delivery)
- [Failed] Detail-panel fields: assignee, labels, due date, start date, reporter, parent, issue type, team (silently dropped)
- [Failed] Rich formatting (bold / lists / code blocks / links) — flattened to plain paragraph