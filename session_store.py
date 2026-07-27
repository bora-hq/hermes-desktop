"""Session persistence layer for Hermes bridge.

SQLite-backed session store. One DB per user (default: ~/.hermes/bridge_sessions.db).
Thread-safe via a single connection protected by a lock.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    preview TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'agent')),
    content TEXT NOT NULL,
    audio_url TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


class SessionStore:
    """Thread-safe SQLite session store."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".hermes" / "bridge_sessions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ── Sessions ──

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Return sessions ordered by updated_at DESC."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, preview, created_at, updated_at, message_count "
                "FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return session metadata or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, preview, created_at, updated_at, message_count "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_session(self, session_id: str, title: str = "", preview: str = "") -> None:
        """Insert or ignore a session record."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, preview) VALUES (?, ?, ?)",
                (session_id, title, preview),
            )
            self._conn.commit()

    def update_session(self, session_id: str, title: str | None = None, preview: str | None = None) -> None:
        """Update title/preview and touch updated_at."""
        with self._lock:
            if title is not None:
                self._conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
                    (title, session_id),
                )
            if preview is not None:
                self._conn.execute(
                    "UPDATE sessions SET preview = ?, updated_at = datetime('now') WHERE id = ?",
                    (preview, session_id),
                )
            self._conn.commit()

    def delete_session(self, session_id: str) -> bool:
        """Delete session + messages. Returns True if session existed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def touch_session(self, session_id: str) -> None:
        """Update updated_at timestamp."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET updated_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            self._conn.commit()

    # ── Messages ──

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        audio_url: str | None = None,
    ) -> None:
        """Append a message and increment session message_count."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, audio_url) VALUES (?, ?, ?, ?)",
                (session_id, role, content, audio_url),
            )
            self._conn.execute(
                "UPDATE sessions SET message_count = message_count + 1, updated_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            self._conn.commit()

    def get_messages(self, session_id: str, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """Return messages for a session, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, role, content, audio_url, created_at "
                "FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_message_preview(self, session_id: str) -> str:
        """Return the last user message content (truncated) for preview."""
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not row:
            return ""
        text = row["content"]
        return text[:80] + ("..." if len(text) > 80 else "")
