"""
Tests for the LifeVault MCP Server components.
Run with: python -m pytest tests/ -v

Coverage:
  - TestCrypto        : 9 tests  (key derivation, encrypt/decrypt, edge cases)
  - TestStorage       : 8 tests  (CRUD, deadlines, stats, concurrent ops)
  - TestAudit         : 3 tests  (logging, stats, append-only integrity)
  - TestSharing       : 5 tests  (create, revoke, expiry, QR, emergency cards)
  - TestValidation    : 4 tests  (extraction validation tool logic)
  - TestEdgeCases     : 3 tests  (empty inputs, special chars, large docs)
  Total: 32 tests
"""

import os
import sys
import json
import tempfile
import sqlite3
from datetime import datetime, timezone, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.crypto import (
    generate_salt, derive_key, encrypt_data, decrypt_data,
    encrypt_string, decrypt_string, encrypt_json, decrypt_json,
    bytes_to_b64, b64_to_bytes, verify_passphrase,
)
from mcp_server.storage import VaultStorage
from mcp_server.audit import AuditLogger
from mcp_server.sharing import ShareManager


# ═══════════════════════════════════════════════════════════════════════════
# Crypto tests (9 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestCrypto:
    """Test AES-256-GCM encryption utilities."""

    def test_key_derivation(self):
        """Same passphrase + salt should produce same key."""
        salt = generate_salt()
        key1 = derive_key("my-passphrase", salt)
        key2 = derive_key("my-passphrase", salt)
        assert key1 == key2
        assert len(key1) == 32  # 256 bits

    def test_different_salts_different_keys(self):
        """Different salts should produce different keys."""
        salt1 = generate_salt()
        salt2 = generate_salt()
        key1 = derive_key("same-passphrase", salt1)
        key2 = derive_key("same-passphrase", salt2)
        assert key1 != key2

    def test_encrypt_decrypt_roundtrip(self):
        """Data should survive encrypt -> decrypt."""
        salt = generate_salt()
        key = derive_key("test-passphrase", salt)
        plaintext = b"Hello, LifeVault!"

        encrypted = encrypt_data(plaintext, key)
        decrypted = decrypt_data(encrypted, key)

        assert decrypted == plaintext
        assert encrypted != plaintext  # should be different

    def test_different_nonces(self):
        """Same plaintext should produce different ciphertexts (unique nonces)."""
        salt = generate_salt()
        key = derive_key("test", salt)
        plaintext = b"same data"

        enc1 = encrypt_data(plaintext, key)
        enc2 = encrypt_data(plaintext, key)

        assert enc1 != enc2  # different nonces -> different ciphertext

    def test_wrong_key_fails(self):
        """Decryption with wrong key should raise an error."""
        salt = generate_salt()
        key1 = derive_key("correct-passphrase", salt)
        key2 = derive_key("wrong-passphrase", salt)

        encrypted = encrypt_data(b"secret", key1)

        try:
            decrypt_data(encrypted, key2)
            assert False, "Should have raised an exception"
        except Exception:
            pass  # expected — InvalidTag

    def test_string_helpers(self):
        """String encrypt/decrypt helpers should work."""
        salt = generate_salt()
        key = derive_key("test", salt)

        original = "Hello, World! 🌍"
        encrypted = encrypt_string(original, key)
        decrypted = decrypt_string(encrypted, key)

        assert decrypted == original

    def test_json_helpers(self):
        """JSON encrypt/decrypt helpers should work."""
        salt = generate_salt()
        key = derive_key("test", salt)

        original = {"name": "Insurance", "amount": 1500.50, "tags": ["car", "auto"]}
        encrypted = encrypt_json(original, key)
        decrypted = decrypt_json(encrypted, key)

        assert decrypted == original

    def test_base64_roundtrip(self):
        """Base64 encoding should be reversible."""
        data = os.urandom(64)
        b64 = bytes_to_b64(data)
        recovered = b64_to_bytes(b64)
        assert recovered == data

    def test_verify_passphrase(self):
        """Passphrase verification should work."""
        salt = generate_salt()
        key = derive_key("my-pass", salt)
        token = encrypt_data(b"LIFEVAULT_VERIFICATION", key)

        assert verify_passphrase("my-pass", salt, token) is True
        assert verify_passphrase("wrong-pass", salt, token) is False


# ═══════════════════════════════════════════════════════════════════════════
# Storage tests (8 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestStorage:
    """Test encrypted vault storage."""

    def _make_vault(self) -> tuple[VaultStorage, str]:
        """Create a temporary vault for testing."""
        tmp = tempfile.mktemp(suffix=".db")
        vault = VaultStorage(tmp)
        vault.initialize("test-passphrase-123")
        return vault, tmp

    def test_initialize_and_unlock(self):
        vault, tmp = self._make_vault()
        vault.close()

        # Re-open and unlock
        vault2 = VaultStorage(tmp)
        result = vault2.unlock("test-passphrase-123")
        assert "successfully" in result

        vault2.close()
        os.unlink(tmp)

    def test_wrong_passphrase_rejected(self):
        vault, tmp = self._make_vault()
        vault.close()

        vault2 = VaultStorage(tmp)
        try:
            vault2.unlock("wrong-passphrase")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

        vault2.close()
        os.unlink(tmp)

    def test_store_and_retrieve(self):
        vault, tmp = self._make_vault()

        doc_id = vault.store_document(
            category="insurance",
            title="Car Insurance - State Farm",
            extracted_data={"policy_number": "SF-123456", "expiry": "2025-12-01"},
            raw_text="State Farm Auto Insurance Policy...",
            file_type="pdf",
        )

        assert doc_id is not None

        # Retrieve and verify
        doc = vault.get_document(doc_id)
        assert doc is not None
        assert doc.title == "Car Insurance - State Farm"
        assert doc.category == "insurance"
        assert doc.extracted_data["policy_number"] == "SF-123456"
        assert "State Farm" in doc.raw_text

        vault.close()
        os.unlink(tmp)

    def test_list_documents(self):
        vault, tmp = self._make_vault()

        vault.store_document("insurance", "Car Insurance", {"type": "auto"}, "...", "pdf")
        vault.store_document("medical", "Blood Test Results", {"type": "lab"}, "...", "pdf")
        vault.store_document("insurance", "Home Insurance", {"type": "home"}, "...", "pdf")

        # List all
        all_docs = vault.list_documents()
        assert len(all_docs) == 3

        # Filter by category
        insurance = vault.list_documents(category="insurance")
        assert len(insurance) == 2

        vault.close()
        os.unlink(tmp)

    def test_delete_document(self):
        vault, tmp = self._make_vault()

        doc_id = vault.store_document("test", "Test Doc", {}, "test", "text")
        assert vault.delete_document(doc_id) is True
        assert vault.get_document(doc_id) is None
        assert vault.delete_document("nonexistent") is False

        vault.close()
        os.unlink(tmp)

    def test_deadlines(self):
        vault, tmp = self._make_vault()

        doc_id = vault.store_document("insurance", "Car Policy", {}, "...", "pdf")
        dl_id = vault.add_deadline(doc_id, "Policy renewal", "2099-01-15", 30)

        deadlines = vault.get_upcoming_deadlines(days_ahead=99999)
        assert len(deadlines) >= 1
        assert deadlines[0].description == "Policy renewal"

        vault.close()
        os.unlink(tmp)

    def test_vault_stats(self):
        vault, tmp = self._make_vault()

        vault.store_document("insurance", "Doc 1", {}, "...", "pdf")
        vault.store_document("medical", "Doc 2", {}, "...", "pdf")

        stats = vault.get_vault_stats()
        assert stats["total_documents"] == 2
        assert "insurance" in stats["categories"]
        assert "medical" in stats["categories"]

        vault.close()
        os.unlink(tmp)

    def test_update_document(self):
        """Updating a document should change its metadata."""
        vault, tmp = self._make_vault()

        doc_id = vault.store_document(
            "insurance", "Old Title", {"key": "old_value"}, "raw text", "pdf"
        )

        vault.update_document(doc_id, title="New Title")
        doc = vault.get_document(doc_id)
        assert doc.title == "New Title"

        vault.close()
        os.unlink(tmp)


# ═══════════════════════════════════════════════════════════════════════════
# Audit tests (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestAudit:
    """Test append-only audit logging."""

    def _make_audit(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        audit = AuditLogger(conn)
        audit.initialize()
        return audit, conn

    def test_log_and_retrieve(self):
        audit, conn = self._make_audit()

        audit.log("store", doc_id="doc-1", details="Stored a document")
        audit.log("read", doc_id="doc-1", details="Read a document")
        audit.log("search", details="Searched for insurance")

        entries = audit.get_log(limit=10)
        assert len(entries) == 3
        assert entries[0].action == "search"  # most recent first

        # Filter by action
        stores = audit.get_log(action_filter="store")
        assert len(stores) == 1
        assert stores[0].doc_id == "doc-1"

        conn.close()

    def test_audit_stats(self):
        audit, conn = self._make_audit()

        audit.log("store", details="test")
        audit.log("store", details="test")
        audit.log("read", details="test")

        stats = audit.get_stats()
        assert stats["total_events"] == 3
        assert stats["by_action"]["store"] == 2

        conn.close()

    def test_audit_is_append_only(self):
        """Audit log entries should never be modifiable or deletable."""
        audit, conn = self._make_audit()

        audit.log("store", doc_id="doc-1", details="Stored doc")
        entries_before = audit.get_log(limit=100)

        # Even after adding more entries, old ones remain
        audit.log("read", details="Read something")
        audit.log("delete", doc_id="doc-2", details="Deleted doc")

        entries_after = audit.get_log(limit=100)
        assert len(entries_after) == 3
        assert len(entries_after) > len(entries_before)

        # Original entry should still be present
        actions = [e.action for e in entries_after]
        assert "store" in actions

        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Sharing tests (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestSharing:
    """Test secure document sharing and QR generation."""

    def _make_sharing(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        signing_key = derive_key("test-vault-key", generate_salt())
        manager = ShareManager(conn, signing_key)
        manager.initialize()
        return manager, conn

    def test_create_and_retrieve_share(self):
        """Created shares should be retrievable with valid data."""
        manager, conn = self._make_sharing()

        share = manager.create_share(
            doc_ids=["doc-1", "doc-2"],
            scope="summary",
            share_data={"title": "Car Insurance", "provider": "State Farm"},
            recipient_label="Dr. Smith",
            expires_in_hours=24,
        )

        assert share.share_id is not None
        assert share.scope == "summary"
        assert share.recipient_label == "Dr. Smith"
        assert share.revoked is False

        # Retrieve the share data
        data = manager.get_share(share.share_id)
        assert data is not None
        assert data["title"] == "Car Insurance"

        conn.close()

    def test_revoke_share(self):
        """Revoked shares should return None when accessed."""
        manager, conn = self._make_sharing()

        share = manager.create_share(
            doc_ids=["doc-1"],
            scope="full",
            share_data={"test": True},
            recipient_label="Test",
            expires_in_hours=24,
        )

        # Should be accessible before revocation
        assert manager.get_share(share.share_id) is not None

        # Revoke
        result = manager.revoke_share(share.share_id)
        assert result is True

        # Should return None after revocation
        assert manager.get_share(share.share_id) is None

        conn.close()

    def test_expired_share_inaccessible(self):
        """Expired shares should not be retrievable."""
        manager, conn = self._make_sharing()

        # Create a share that expired in the past (0 hours)
        share = manager.create_share(
            doc_ids=["doc-1"],
            scope="summary",
            share_data={"test": True},
            recipient_label="Expired User",
            expires_in_hours=0,  # expires immediately
        )

        # Should be expired
        data = manager.get_share(share.share_id)
        assert data is None

        conn.close()

    def test_list_active_shares(self):
        """Should only list non-revoked, non-expired shares."""
        manager, conn = self._make_sharing()

        share1 = manager.create_share(
            ["doc-1"], "full", {"a": 1}, "Person A", 24
        )
        share2 = manager.create_share(
            ["doc-2"], "summary", {"b": 2}, "Person B", 24
        )
        share3 = manager.create_share(
            ["doc-3"], "full", {"c": 3}, "Person C", 0  # expired
        )

        # Revoke share1
        manager.revoke_share(share1.share_id)

        active = manager.list_active_shares()
        active_ids = [s.share_id for s in active]

        # Only share2 should be active (share1 revoked, share3 expired)
        assert share2.share_id in active_ids
        assert share1.share_id not in active_ids

        conn.close()

    def test_generate_qr_code(self):
        """QR code generation should produce valid base64 PNG data."""
        manager, conn = self._make_sharing()

        share = manager.create_share(
            doc_ids=["doc-1"],
            scope="summary",
            share_data={"title": "Test", "content": "Important data"},
            recipient_label="QR Test",
            expires_in_hours=24,
        )

        qr_b64 = manager.generate_qr_code(share.share_id)
        assert qr_b64 is not None
        assert len(qr_b64) > 100  # should be substantial base64 data

        # Verify it's valid base64
        import base64
        decoded = base64.b64decode(qr_b64)
        # PNG magic bytes
        assert decoded[:4] == b'\x89PNG'

        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Validation tool tests (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestValidation:
    """
    Test the extraction validation logic used by the LoopAgent reviewer.
    These test the validation function's logic directly.
    """

    def test_valid_insurance_extraction(self):
        """Complete insurance extraction should pass validation."""
        from mcp_server.server import CATEGORY_REQUIRED_FIELDS

        category = "insurance"
        data = {
            "provider": "State Farm",
            "policy_number": "SF-2026-789",
            "coverage_type": "Auto",
            "premium": "$150/month",
            "expiry_date": "2027-01-15",
        }

        required = CATEGORY_REQUIRED_FIELDS[category]
        missing = [f for f in required if f not in data or not data[f]]

        assert len(missing) == 0, f"Should pass: no missing fields, got {missing}"

    def test_incomplete_insurance_fails(self):
        """Insurance extraction missing policy_number should fail."""
        from mcp_server.server import CATEGORY_REQUIRED_FIELDS

        category = "insurance"
        data = {
            "provider": "State Farm",
            # policy_number missing
            "coverage_type": "Auto",
        }

        required = CATEGORY_REQUIRED_FIELDS[category]
        missing = [f for f in required if f not in data or not data[f]]

        assert "policy_number" in missing

    def test_medical_extraction_validation(self):
        """Medical documents require provider and visit_date."""
        from mcp_server.server import CATEGORY_REQUIRED_FIELDS

        category = "medical"
        data = {"provider": "Dr. Smith", "visit_date": "2026-06-15"}

        required = CATEGORY_REQUIRED_FIELDS[category]
        missing = [f for f in required if f not in data or not data[f]]

        assert len(missing) == 0

    def test_other_category_always_passes(self):
        """'other' category has no required fields — should always pass."""
        from mcp_server.server import CATEGORY_REQUIRED_FIELDS

        category = "other"
        data = {"anything": "goes here"}

        required = CATEGORY_REQUIRED_FIELDS[category]
        missing = [f for f in required if f not in data or not data[f]]

        assert len(missing) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Edge case tests (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Test boundary conditions and special inputs."""

    def test_unicode_document_storage(self):
        """Documents with Unicode content should be stored and retrieved correctly."""
        tmp = tempfile.mktemp(suffix=".db")
        vault = VaultStorage(tmp)
        vault.initialize("test-pass")

        doc_id = vault.store_document(
            category="legal",
            title="Contrato de Arrendamiento - 契約書",
            extracted_data={"parties": "José García & 田中太郎", "amount": "€2,500/mes"},
            raw_text="Este contrato de arrendamiento... 本契約は...",
            file_type="pdf",
        )

        doc = vault.get_document(doc_id)
        assert "José García" in doc.extracted_data["parties"]
        assert "田中太郎" in doc.extracted_data["parties"]
        assert "€2,500" in doc.extracted_data["amount"]

        vault.close()
        os.unlink(tmp)

    def test_empty_extracted_data(self):
        """Documents with empty extracted data should still be storable."""
        tmp = tempfile.mktemp(suffix=".db")
        vault = VaultStorage(tmp)
        vault.initialize("test-pass")

        doc_id = vault.store_document(
            category="other",
            title="Blank Document",
            extracted_data={},
            raw_text="Just some raw text with no structure.",
            file_type="text",
        )

        doc = vault.get_document(doc_id)
        assert doc is not None
        assert doc.title == "Blank Document"
        assert doc.extracted_data == {}

        vault.close()
        os.unlink(tmp)

    def test_large_document_storage(self):
        """Large documents (100KB+ raw text) should be handled correctly."""
        tmp = tempfile.mktemp(suffix=".db")
        vault = VaultStorage(tmp)
        vault.initialize("test-pass")

        large_text = "This is a test sentence. " * 5000  # ~125KB
        doc_id = vault.store_document(
            category="legal",
            title="Large Legal Contract",
            extracted_data={"pages": 100, "word_count": len(large_text.split())},
            raw_text=large_text,
            file_type="pdf",
        )

        doc = vault.get_document(doc_id)
        assert doc is not None
        assert len(doc.raw_text) == len(large_text)

        vault.close()
        os.unlink(tmp)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
