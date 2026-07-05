"""Encrypted storage layer for LifeVault.

This module provides the persistence backend used by the MCP server. It stores
plaintext metadata for efficient filtering while encrypting the sensitive
content fields such as document text, extracted structured data, and
embeddings.
"""

import sqlite3
import uuid
import json
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field, asdict

from .crypto import (
    generate_salt,
    derive_key,
    encrypt_data,
    decrypt_data,
    encrypt_json,
    decrypt_json,
    encrypt_string,
    decrypt_string,
    bytes_to_b64,
    b64_to_bytes,
    verify_passphrase,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """Represents a document stored in the vault."""
    doc_id: str
    category: str            # plaintext — for filtering
    title: str               # plaintext — for display
    extracted_data: dict     # decrypted structured data
    raw_text: str            # decrypted original text
    file_type: str           # e.g., "pdf", "image", "text"
    created_at: str          # ISO-8601 UTC
    updated_at: str          # ISO-8601 UTC


@dataclass
class Deadline:
    """Represents a tracked deadline from a document."""
    deadline_id: str
    doc_id: str
    description: str
    deadline_date: str       # ISO-8601 date (YYYY-MM-DD)
    alert_days_before: int   # How many days before to alert
    status: str              # "active", "completed", "dismissed"
    created_at: str


@dataclass
class DocumentSummary:
    """Lightweight document info for listing (no decryption needed)."""
    doc_id: str
    category: str
    title: str
    file_type: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vault_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id              TEXT PRIMARY KEY,
    category            TEXT NOT NULL,
    title               TEXT NOT NULL,
    encrypted_data      TEXT NOT NULL,    -- base64(AES-256-GCM encrypted JSON)
    encrypted_raw_text  TEXT NOT NULL,    -- base64(AES-256-GCM encrypted text)
    encrypted_embedding TEXT,             -- base64(AES-256-GCM encrypted float array)
    file_type           TEXT NOT NULL DEFAULT 'text',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deadlines (
    deadline_id        TEXT PRIMARY KEY,
    doc_id             TEXT NOT NULL,
    description        TEXT NOT NULL,
    deadline_date      TEXT NOT NULL,     -- YYYY-MM-DD
    alert_days_before  INTEGER NOT NULL DEFAULT 30,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT NOT NULL,
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_deadlines_date ON deadlines(deadline_date);
CREATE INDEX IF NOT EXISTS idx_deadlines_status ON deadlines(status);
"""


# ---------------------------------------------------------------------------
# VaultStorage implementation
# ---------------------------------------------------------------------------

class VaultStorage:
    """
    Encrypted document storage backed by SQLite.

    Usage:
        vault = VaultStorage("vault.db")
        vault.initialize("my-secure-passphrase")  # first time
        vault.unlock("my-secure-passphrase")       # subsequent times
        vault.store_document(...)
    """

    def __init__(self, db_path: str = "vault.db"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._key: Optional[bytes] = None  # encryption key — memory only
        self._is_unlocked = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        """Close the database connection and clear the key from memory."""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._key = None
        self._is_unlocked = False

    # ------------------------------------------------------------------
    # Vault lifecycle
    # ------------------------------------------------------------------

    def is_initialized(self) -> bool:
        """Check if the vault database has been set up."""
        if not Path(self.db_path).exists():
            return False
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vault_config'"
        )
        return cursor.fetchone() is not None

    def initialize(self, passphrase: str) -> str:
        """
        Initialize a new vault with the given passphrase.

        Creates the database schema, generates a salt, derives the key,
        and stores a verification token for future unlocks.

        Returns:
            Confirmation message
        """
        if self.is_initialized():
            return "Vault is already initialized. Use unlock() instead."

        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)

        # Generate salt and derive key
        salt = generate_salt()
        self._key = derive_key(passphrase, salt)

        # Create verification token (encrypted known plaintext)
        verification = encrypt_data(b"LIFEVAULT_VERIFICATION", self._key)

        # Store config
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO vault_config (key, value) VALUES (?, ?)",
            ("salt", bytes_to_b64(salt))
        )
        conn.execute(
            "INSERT INTO vault_config (key, value) VALUES (?, ?)",
            ("verification_token", bytes_to_b64(verification))
        )
        conn.execute(
            "INSERT INTO vault_config (key, value) VALUES (?, ?)",
            ("created_at", now)
        )
        conn.commit()

        self._is_unlocked = True
        return f"Vault initialized successfully at {now}"

    def unlock(self, passphrase: str) -> str:
        """
        Unlock an existing vault by verifying the passphrase.

        Returns:
            Confirmation message

        Raises:
            ValueError: If passphrase is incorrect
            RuntimeError: If vault is not initialized
        """
        if not self.is_initialized():
            raise RuntimeError("Vault not initialized. Call initialize() first.")

        conn = self._get_conn()

        # Retrieve salt and verification token
        salt_row = conn.execute(
            "SELECT value FROM vault_config WHERE key = 'salt'"
        ).fetchone()
        verify_row = conn.execute(
            "SELECT value FROM vault_config WHERE key = 'verification_token'"
        ).fetchone()

        salt = b64_to_bytes(salt_row["value"])
        verification = b64_to_bytes(verify_row["value"])

        # Verify passphrase
        if not verify_passphrase(passphrase, salt, verification):
            raise ValueError("Incorrect passphrase.")

        self._key = derive_key(passphrase, salt)
        self._is_unlocked = True
        return "Vault unlocked successfully."

    def _require_unlocked(self):
        """Guard: ensure vault is unlocked before any data operation."""
        if not self._is_unlocked or self._key is None:
            raise RuntimeError("Vault is locked. Call unlock() first.")

    # ------------------------------------------------------------------
    # Document CRUD
    # ------------------------------------------------------------------

    def store_document(
        self,
        category: str,
        title: str,
        extracted_data: dict,
        raw_text: str,
        file_type: str = "text",
        embedding: Optional[list[float]] = None,
    ) -> str:
        """
        Store a new encrypted document in the vault.

        Args:
            category: Document category (insurance, medical, legal, etc.)
            title: Human-readable title
            extracted_data: Structured data extracted by the Document Agent
            raw_text: Original text content
            file_type: Source file type (pdf, image, text)
            embedding: Optional semantic embedding vector

        Returns:
            The generated document ID (UUID)
        """
        self._require_unlocked()
        conn = self._get_conn()

        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Encrypt content
        enc_data = bytes_to_b64(encrypt_json(extracted_data, self._key))
        enc_text = bytes_to_b64(encrypt_string(raw_text, self._key))

        # Encrypt embedding if provided
        enc_embedding = None
        if embedding:
            emb_bytes = json.dumps(embedding).encode("utf-8")
            enc_embedding = bytes_to_b64(encrypt_data(emb_bytes, self._key))

        conn.execute(
            """INSERT INTO documents
               (doc_id, category, title, encrypted_data, encrypted_raw_text,
                encrypted_embedding, file_type, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, category, title, enc_data, enc_text,
             enc_embedding, file_type, now, now)
        )
        conn.commit()
        return doc_id

    def get_document(self, doc_id: str) -> Optional[Document]:
        """
        Retrieve and decrypt a document by ID.

        Returns:
            Document object with decrypted content, or None if not found
        """
        self._require_unlocked()
        conn = self._get_conn()

        row = conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()

        if not row:
            return None

        return Document(
            doc_id=row["doc_id"],
            category=row["category"],
            title=row["title"],
            extracted_data=decrypt_json(b64_to_bytes(row["encrypted_data"]), self._key),
            raw_text=decrypt_string(b64_to_bytes(row["encrypted_raw_text"]), self._key),
            file_type=row["file_type"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_documents(
        self,
        category: Optional[str] = None,
        sort_by: str = "updated_at",
    ) -> list[DocumentSummary]:
        """
        List documents without decrypting content (fast).

        Args:
            category: Optional filter by category
            sort_by: Sort field (created_at or updated_at)

        Returns:
            List of DocumentSummary objects
        """
        self._require_unlocked()
        conn = self._get_conn()

        if category:
            rows = conn.execute(
                f"SELECT doc_id, category, title, file_type, created_at, updated_at "
                f"FROM documents WHERE category = ? ORDER BY {sort_by} DESC",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT doc_id, category, title, file_type, created_at, updated_at "
                f"FROM documents ORDER BY {sort_by} DESC"
            ).fetchall()

        return [
            DocumentSummary(
                doc_id=r["doc_id"],
                category=r["category"],
                title=r["title"],
                file_type=r["file_type"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def update_document(
        self,
        doc_id: str,
        category: Optional[str] = None,
        title: Optional[str] = None,
        extracted_data: Optional[dict] = None,
        raw_text: Optional[str] = None,
        embedding: Optional[list[float]] = None,
    ) -> bool:
        """
        Update an existing document. Only provided fields are changed.

        Returns:
            True if document was found and updated, False otherwise
        """
        self._require_unlocked()
        conn = self._get_conn()

        # Check document exists
        existing = conn.execute(
            "SELECT doc_id FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if not existing:
            return False

        updates = []
        params = []

        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if extracted_data is not None:
            updates.append("encrypted_data = ?")
            params.append(bytes_to_b64(encrypt_json(extracted_data, self._key)))
        if raw_text is not None:
            updates.append("encrypted_raw_text = ?")
            params.append(bytes_to_b64(encrypt_string(raw_text, self._key)))
        if embedding is not None:
            emb_bytes = json.dumps(embedding).encode("utf-8")
            updates.append("encrypted_embedding = ?")
            params.append(bytes_to_b64(encrypt_data(emb_bytes, self._key)))

        if not updates:
            return True  # nothing to update

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(doc_id)

        conn.execute(
            f"UPDATE documents SET {', '.join(updates)} WHERE doc_id = ?",
            params
        )
        conn.commit()
        return True

    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document and its associated deadlines.

        Returns:
            True if document was found and deleted, False otherwise
        """
        self._require_unlocked()
        conn = self._get_conn()

        cursor = conn.execute(
            "DELETE FROM documents WHERE doc_id = ?", (doc_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_document_count(self) -> int:
        """Get total number of documents in the vault."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Deadline operations
    # ------------------------------------------------------------------

    def add_deadline(
        self,
        doc_id: str,
        description: str,
        deadline_date: str,
        alert_days_before: int = 30,
    ) -> str:
        """
        Add a deadline linked to a document.

        Args:
            doc_id: Parent document ID
            description: What this deadline is for
            deadline_date: YYYY-MM-DD format
            alert_days_before: Days before deadline to trigger alert

        Returns:
            Generated deadline ID
        """
        self._require_unlocked()
        conn = self._get_conn()

        deadline_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO deadlines
               (deadline_id, doc_id, description, deadline_date,
                alert_days_before, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (deadline_id, doc_id, description, deadline_date,
             alert_days_before, now)
        )
        conn.commit()
        return deadline_id

    def get_upcoming_deadlines(self, days_ahead: int = 90) -> list[Deadline]:
        """
        Get all active deadlines within the next N days.

        Args:
            days_ahead: Look-ahead window in days

        Returns:
            List of Deadline objects sorted by date
        """
        self._require_unlocked()
        conn = self._get_conn()

        rows = conn.execute(
            """SELECT d.*, doc.title as doc_title
               FROM deadlines d
               JOIN documents doc ON d.doc_id = doc.doc_id
               WHERE d.status = 'active'
                 AND d.deadline_date <= date('now', '+' || ? || ' days')
                 AND d.deadline_date >= date('now')
               ORDER BY d.deadline_date ASC""",
            (days_ahead,)
        ).fetchall()

        return [
            Deadline(
                deadline_id=r["deadline_id"],
                doc_id=r["doc_id"],
                description=r["description"],
                deadline_date=r["deadline_date"],
                alert_days_before=r["alert_days_before"],
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def update_deadline_status(self, deadline_id: str, status: str) -> bool:
        """Update a deadline's status (active/completed/dismissed)."""
        self._require_unlocked()
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE deadlines SET status = ? WHERE deadline_id = ?",
            (status, deadline_id)
        )
        conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Embedding retrieval (for semantic search)
    # ------------------------------------------------------------------

    def get_all_embeddings(self) -> list[tuple[str, list[float]]]:
        """
        Retrieve and decrypt all document embeddings for semantic search.

        Returns:
            List of (doc_id, embedding_vector) tuples

        Performance note:
            At personal-vault scale (~500 docs), decrypting all embeddings
            takes <10ms. For larger vaults, consider FAISS/ScaNN with
            encrypted shards.
        """
        self._require_unlocked()
        conn = self._get_conn()

        rows = conn.execute(
            "SELECT doc_id, encrypted_embedding FROM documents "
            "WHERE encrypted_embedding IS NOT NULL"
        ).fetchall()

        results = []
        for row in rows:
            emb_bytes = decrypt_data(b64_to_bytes(row["encrypted_embedding"]), self._key)
            embedding = json.loads(emb_bytes.decode("utf-8"))
            results.append((row["doc_id"], embedding))

        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_vault_stats(self) -> dict:
        """Get vault statistics for the dashboard."""
        conn = self._get_conn()

        total = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]

        categories = conn.execute(
            "SELECT category, COUNT(*) as c FROM documents GROUP BY category"
        ).fetchall()

        active_deadlines = conn.execute(
            "SELECT COUNT(*) as c FROM deadlines WHERE status = 'active'"
        ).fetchone()["c"]

        return {
            "total_documents": total,
            "categories": {r["category"]: r["c"] for r in categories},
            "active_deadlines": active_deadlines,
        }
