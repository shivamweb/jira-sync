import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS issue_map (
    source_key TEXT PRIMARY KEY,
    target_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comment_map (
    source_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachment_map (
    source_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get_issue(self, source_key):
        with self._conn() as c:
            row = c.execute(
                "SELECT target_key FROM issue_map WHERE source_key=?", (source_key,)
            ).fetchone()
            return row[0] if row else None

    def put_issue(self, source_key, target_key):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO issue_map(source_key, target_key) VALUES (?, ?)",
                (source_key, target_key),
            )

    def get_comment(self, source_id):
        with self._conn() as c:
            row = c.execute(
                "SELECT target_id FROM comment_map WHERE source_id=?", (source_id,)
            ).fetchone()
            return row[0] if row else None

    def put_comment(self, source_id, target_id):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO comment_map(source_id, target_id) VALUES (?, ?)",
                (source_id, target_id),
            )

    def get_attachment(self, source_id):
        with self._conn() as c:
            row = c.execute(
                "SELECT target_id FROM attachment_map WHERE source_id=?", (source_id,)
            ).fetchone()
            return row[0] if row else None

    def put_attachment(self, source_id, target_id):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO attachment_map(source_id, target_id) VALUES (?, ?)",
                (source_id, target_id),
            )
