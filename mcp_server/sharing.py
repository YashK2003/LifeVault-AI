"""Secure sharing primitives for LifeVault.

The sharing module manages time-limited shares and QR-code-based artifacts.
It is designed to support scoped document sharing and emergency-card-style
exports while preserving the integrity and expiry semantics of each share.
"""

import sqlite3
import uuid
import hmac
import hashlib
import json
import io
import base64
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass

import qrcode
from qrcode.constants import ERROR_CORRECT_H


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class Share:
    """Represents an active or expired share."""
    share_id: str
    doc_ids: list[str]
    scope: str             # "full", "summary", "emergency"
    recipient_label: str   # "Dr. Smith", "Mom", "ER"
    created_at: str
    expires_at: str
    revoked: bool


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SHARING_SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    share_id        TEXT PRIMARY KEY,
    doc_ids         TEXT NOT NULL,       -- JSON array of doc IDs
    scope           TEXT NOT NULL,
    recipient_label TEXT NOT NULL,
    share_data      TEXT NOT NULL,       -- The actual shared content (encrypted)
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    revoked         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shares_expires ON shares(expires_at);
"""


# ---------------------------------------------------------------------------
# ShareManager class
# ---------------------------------------------------------------------------

class ShareManager:
    """
    Manages secure, time-limited document sharing.

    Usage:
        manager = ShareManager(db_connection, signing_key)
        manager.initialize()
        share = manager.create_share(
            doc_ids=["abc-123"],
            scope="summary",
            share_data={"title": "Insurance", "expiry": "2025-01-01"},
            recipient_label="Dr. Smith",
            expires_in_hours=24,
        )
    """

    def __init__(self, conn: sqlite3.Connection, signing_key: bytes):
        self._conn = conn
        self._signing_key = signing_key  # derived from vault key

    def initialize(self):
        """Create the shares table if it doesn't exist."""
        self._conn.executescript(SHARING_SCHEMA)

    # ------------------------------------------------------------------
    # Create / Revoke
    # ------------------------------------------------------------------

    def create_share(
        self,
        doc_ids: list[str],
        scope: str,
        share_data: dict,
        recipient_label: str,
        expires_in_hours: int = 24,
    ) -> Share:
        """
        Create a new time-limited share.

        Args:
            doc_ids: Documents included in this share
            scope: What level of detail ("full", "summary", "emergency")
            share_data: The actual data to share (already scoped by caller)
            recipient_label: Who this share is for (display only)
            expires_in_hours: Hours until automatic expiration

        Returns:
            Share object with all details
        """
        share_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=expires_in_hours)

        # Sign the share data for integrity verification
        signed_payload = self._sign_share(share_id, share_data, expires_at.isoformat())

        self._conn.execute(
            """INSERT INTO shares
               (share_id, doc_ids, scope, recipient_label,
                share_data, created_at, expires_at, revoked)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                share_id,
                json.dumps(doc_ids),
                scope,
                recipient_label,
                json.dumps(signed_payload),
                now.isoformat(),
                expires_at.isoformat(),
            )
        )
        self._conn.commit()

        return Share(
            share_id=share_id,
            doc_ids=doc_ids,
            scope=scope,
            recipient_label=recipient_label,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            revoked=False,
        )

    def revoke_share(self, share_id: str) -> bool:
        """
        Revoke a share immediately, regardless of expiration.

        Returns:
            True if share was found and revoked
        """
        cursor = self._conn.execute(
            "UPDATE shares SET revoked = 1 WHERE share_id = ?",
            (share_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Retrieve
    # ------------------------------------------------------------------

    def get_share(self, share_id: str) -> Optional[dict]:
        """
        Retrieve share data if the share is still valid (not expired, not revoked).

        Returns:
            Shared data dict, or None if invalid/expired/revoked
        """
        row = self._conn.execute(
            "SELECT * FROM shares WHERE share_id = ?", (share_id,)
        ).fetchone()

        if not row:
            return None

        # Check revocation
        if row["revoked"]:
            return None

        # Check expiration
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            return None

        # Verify signature integrity
        signed_payload = json.loads(row["share_data"])
        if not self._verify_share(signed_payload):
            return None

        return signed_payload["data"]

    def list_active_shares(self) -> list[Share]:
        """List all non-revoked, non-expired shares."""
        now = datetime.now(timezone.utc).isoformat()

        rows = self._conn.execute(
            """SELECT * FROM shares
               WHERE revoked = 0 AND expires_at > ?
               ORDER BY created_at DESC""",
            (now,)
        ).fetchall()

        return [
            Share(
                share_id=r["share_id"],
                doc_ids=json.loads(r["doc_ids"]),
                scope=r["scope"],
                recipient_label=r["recipient_label"],
                created_at=r["created_at"],
                expires_at=r["expires_at"],
                revoked=bool(r["revoked"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # QR Code generation
    # ------------------------------------------------------------------

    def generate_qr_code(self, share_id: str) -> Optional[str]:
        """
        Generate a QR code for a share as a base64-encoded PNG.

        The QR code contains a JSON payload with:
        - share_id for lookup
        - scope and recipient info
        - A compact summary of the shared data

        Returns:
            Base64-encoded PNG string, or None if share is invalid
        """
        share_data = self.get_share(share_id)
        if share_data is None:
            return None

        # Get share metadata
        row = self._conn.execute(
            "SELECT * FROM shares WHERE share_id = ?", (share_id,)
        ).fetchone()

        # Build QR payload (keep it compact for QR readability)
        qr_payload = {
            "lifevault_share": share_id,
            "recipient": row["recipient_label"],
            "scope": row["scope"],
            "expires": row["expires_at"],
            "data": share_data,
        }

        # Generate QR code
        qr = qrcode.QRCode(
            version=None,  # auto-size
            error_correction=ERROR_CORRECT_H,  # high error correction
            box_size=10,
            border=4,
        )
        qr.add_data(json.dumps(qr_payload, ensure_ascii=True))
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # Convert to base64 PNG
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        b64_png = base64.b64encode(buffer.getvalue()).decode("ascii")

        return b64_png

    def generate_emergency_card(
        self,
        data: dict,
        recipient_label: str = "Emergency Responder",
        expires_in_hours: int = 720,  # 30 days default for emergency cards
    ) -> tuple[Share, str]:
        """
        Generate a special emergency medical card.

        Args:
            data: Dict with keys like "allergies", "conditions",
                  "medications", "emergency_contacts", "blood_type"
            recipient_label: Who the card is intended for
            expires_in_hours: Validity period (default 30 days)

        Returns:
            Tuple of (Share object, base64 QR code PNG)
        """
        share = self.create_share(
            doc_ids=[],  # emergency cards aren't linked to specific docs
            scope="emergency",
            share_data=data,
            recipient_label=recipient_label,
            expires_in_hours=expires_in_hours,
        )

        qr_b64 = self.generate_qr_code(share.share_id)
        return share, qr_b64

    # ------------------------------------------------------------------
    # Signing / Verification (HMAC-SHA256)
    # ------------------------------------------------------------------

    def _sign_share(self, share_id: str, data: dict, expires_at: str) -> dict:
        """Create a signed payload for tamper detection."""
        payload = {
            "share_id": share_id,
            "data": data,
            "expires_at": expires_at,
        }
        # HMAC signature over the canonical JSON
        message = json.dumps(payload, sort_keys=True).encode("utf-8")
        signature = hmac.new(
            self._signing_key, message, hashlib.sha256
        ).hexdigest()

        payload["signature"] = signature
        return payload

    def _verify_share(self, signed_payload: dict) -> bool:
        """Verify the HMAC signature of a share payload."""
        signature = signed_payload.pop("signature", None)
        if not signature:
            return False

        message = json.dumps(signed_payload, sort_keys=True).encode("utf-8")
        expected = hmac.new(
            self._signing_key, message, hashlib.sha256
        ).hexdigest()

        # Restore signature for future use
        signed_payload["signature"] = signature

        return hmac.compare_digest(signature, expected)
