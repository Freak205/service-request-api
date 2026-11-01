"""SQLite persistence for service requests."""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .validation import CATEGORIES, PRIORITIES, STATUSES, RequestCreate


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL CHECK (length(title) BETWEEN 5 AND 100),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    category    TEXT NOT NULL CHECK (category IN ({_sql_list(CATEGORIES)})),
    priority    TEXT NOT NULL CHECK (priority IN ({_sql_list(PRIORITIES)})),
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ({_sql_list(STATUSES)})),
    created_at  TEXT NOT NULL,
    due_date    TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests (status);
CREATE INDEX IF NOT EXISTS idx_requests_due_date ON requests (due_date);
"""

# Status transitions the business rules allow. Anything else is rejected.
ALLOWED_TRANSITIONS = {"open": "in_progress", "in_progress": "resolved"}


class TransitionError(Exception):
    """Raised when a status change is not permitted."""

    def __init__(self, current: str, requested: str):
        self.current = current
        self.requested = requested
        super().__init__(f"Cannot change status from '{current}' to '{requested}'")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RequestRepository:
    """All SQL lives here. A fresh connection is opened per operation."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._ready = False

    def _ensure_ready(self) -> None:
        """Create the database file and schema on first use (idempotent)."""
        if not self._ready:
            self.init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_ready()
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commit on success, roll back on exception
                yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()
        self._ready = True

    # ---- writes -----------------------------------------------------------------
    @staticmethod
    def _insert(conn: sqlite3.Connection, data: RequestCreate) -> int:
        cur = conn.execute(
            "INSERT INTO requests (title, description, category, priority, status, created_at, due_date) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (data.title, data.description, data.category, data.priority, utc_now_iso(), data.due_date),
        )
        return int(cur.lastrowid)

    def create(self, data: RequestCreate) -> dict:
        with self._connect() as conn:
            new_id = self._insert(conn, data)
            return self._get(conn, new_id)

    def create_many(self, items: list[RequestCreate]) -> int:
        """Insert all items in one transaction (all or nothing). Returns the count."""
        with self._connect() as conn:
            for item in items:
                self._insert(conn, item)
        return len(items)

    def update_status(self, request_id: int, new_status: str) -> Optional[dict]:
        """Apply a status change. Returns None if the record does not exist.

        Raises TransitionError for prohibited transitions. Repeating the current
        status is a no-op that leaves timestamps untouched.
        """
        self._ensure_ready()
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")  # serialise concurrent status changes
            try:
                row = self._get(conn, request_id)
                if row is None:
                    conn.execute("ROLLBACK")
                    return None
                current = row["status"]
                if current == new_status:
                    conn.execute("ROLLBACK")
                    return row
                if ALLOWED_TRANSITIONS.get(current) != new_status:
                    conn.execute("ROLLBACK")
                    raise TransitionError(current, new_status)
                resolved_at = utc_now_iso() if new_status == "resolved" else None
                conn.execute(
                    "UPDATE requests SET status = ?, resolved_at = ? WHERE id = ?",
                    (new_status, resolved_at, request_id),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            return self._get(conn, request_id)
        finally:
            conn.close()

    # ---- reads ------------------------------------------------------------------
    @staticmethod
    def _get(conn: sqlite3.Connection, request_id: int) -> Optional[dict]:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def get(self, request_id: int) -> Optional[dict]:
        with self._connect() as conn:
            return self._get(conn, request_id)

    def list(
        self,
        category: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[dict], int]:
        """Filters combine with AND. Returns (items for the page, total matches)."""
        clauses, params = [], []
        for column, value in (("category", category), ("priority", priority), ("status", status)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM requests {where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM requests {where} ORDER BY id ASC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return [dict(r) for r in rows], total

    def summary(self, as_of: date) -> dict:
        """Counts by status and priority (zero-filled) plus the overdue count.

        Overdue: due_date strictly earlier than as_of and status not resolved.
        ISO dates sort lexically, so a string comparison is correct.
        """
        with self._connect() as conn:
            by_status = dict.fromkeys(STATUSES, 0)
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM requests GROUP BY status"):
                by_status[row["status"]] = row["n"]
            by_priority = dict.fromkeys(PRIORITIES, 0)
            for row in conn.execute("SELECT priority, COUNT(*) AS n FROM requests GROUP BY priority"):
                by_priority[row["priority"]] = row["n"]
            overdue = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE due_date < ? AND status != 'resolved'",
                (as_of.isoformat(),),
            ).fetchone()[0]
        return {
            "as_of": as_of.isoformat(),
            "total": sum(by_status.values()),
            "status_counts": by_status,
            "priority_counts": by_priority,
            "overdue_count": overdue,
        }
