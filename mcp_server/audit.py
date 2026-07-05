"""Audit logging for LifeVault.

The audit logger records security-relevant vault operations in an append-only
store. It captures metadata about each action without storing the underlying
sensitive document content.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class AuditEntry:
    """A single audit log entry."""
    log_id: int
    timestamp: str
    action: str         # store, read, search, share, delete, unlock, etc.
    doc_id: Optional[str]
    details: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    log_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action    TEXT NOT NULL,
    doc_id    TEXT,
    details   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
"""


# ---------------------------------------------------------------------------
# AuditLogger class
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    Append-only audit logger for vault operations.

    Usage:
        logger = AuditLogger(db_connection)
        logger.initialize()
        logger.log("store", doc_id="abc-123", details="Stored insurance document")
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def initialize(self):
        """Create the audit log table if it doesn't exist."""
        self._conn.executescript(AUDIT_SCHEMA)

    def log(
        self,
        action: str,
        doc_id: Optional[str] = None,
        details: str = "",
    ):
        """
        Record an audit event.

        Args:
            action: Operation type (store, read, search, share, delete,
                    unlock, create_share, revoke_share, etc.)
            doc_id: Related document ID, if applicable
            details: Human-readable description of what happened
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO audit_log (timestamp, action, doc_id, details) VALUES (?, ?, ?, ?)",
            (timestamp, action, doc_id, details)
        )
        self._conn.commit()

    def get_log(
        self,
        limit: int = 50,
        action_filter: Optional[str] = None,
        doc_id_filter: Optional[str] = None,
    ) -> list[AuditEntry]:
        """
        Retrieve audit log entries.

        Args:
            limit: Maximum entries to return (most recent first)
            action_filter: Filter by action type
            doc_id_filter: Filter by document ID

        Returns:
            List of AuditEntry objects, newest first
        """
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []

        if action_filter:
            query += " AND action = ?"
            params.append(action_filter)
        if doc_id_filter:
            query += " AND doc_id = ?"
            params.append(doc_id_filter)

        query += " ORDER BY log_id DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()

        return [
            AuditEntry(
                log_id=r["log_id"],
                timestamp=r["timestamp"],
                action=r["action"],
                doc_id=r["doc_id"],
                details=r["details"],
            )
            for r in rows
        ]

    def get_stats(self) -> dict:
        """Get audit log statistics."""
        total = self._conn.execute(
            "SELECT COUNT(*) as c FROM audit_log"
        ).fetchone()["c"]

        by_action = self._conn.execute(
            "SELECT action, COUNT(*) as c FROM audit_log GROUP BY action ORDER BY c DESC"
        ).fetchall()

        return {
            "total_events": total,
            "by_action": {r["action"]: r["c"] for r in by_action},
        }
