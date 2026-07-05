"""LifeVault MCP server entry point.

This module exposes the LifeVault capabilities to ADK agents over the Model
Context Protocol using stdio transport. It wires together the persistence,
audit, sharing, and embedding layers into a single tool surface for the
orchestrator and specialist agents.
"""

import os
import json
import hashlib
from pathlib import Path
from dataclasses import asdict

from dotenv import load_dotenv
from fastmcp import FastMCP

from .storage import VaultStorage, Document, DocumentSummary, Deadline
from .audit import AuditLogger
from .sharing import ShareManager
from .embeddings import generate_embedding, generate_query_embedding, search_embeddings

# ---------------------------------------------------------------------------
# Environment and server configuration
# ---------------------------------------------------------------------------

# Load environment variables from the project root so the server behaves
# correctly when launched as a subprocess by ADK.
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

# Default database location is the project root, but deployments may override
# it through the VAULT_DB_PATH environment variable.
VAULT_DB_PATH = os.getenv("VAULT_DB_PATH", str(_project_root / "vault.db"))

# ---------------------------------------------------------------------------
# Initialize shared components
# ---------------------------------------------------------------------------

# Create the FastMCP server instance and provide usage guidance to the agent.
mcp = FastMCP(
    "LifeVault",
    instructions=(
        "LifeVault is a secure personal document vault. "
        "All document content is encrypted with AES-256-GCM. "
        "You must call vault_initialize (first time) or vault_unlock "
        "(returning user) before any document operations. "
        "Every operation is recorded in the audit log."
    ),
)

# Shared storage instance used by all tool handlers.
vault = VaultStorage(VAULT_DB_PATH)

# Audit logging and share management are initialized lazily because they need
# the vault to be unlocked before some derived state is available.
_audit: AuditLogger | None = None
_shares: ShareManager | None = None


def _ensure_audit() -> AuditLogger:
    """Initialize the audit logger on first use."""
    global _audit
    if _audit is None:
        _audit = AuditLogger(vault._get_conn())
        _audit.initialize()  # Creates audit_log table if not exists
    return _audit


def _ensure_shares() -> ShareManager:
    """Initialize the share manager on first use."""
    global _shares
    if _shares is None:
        # Derive a separate signing key from the vault key using HKDF-like pattern.
        # This ensures share tokens can't be forged without the vault passphrase.
        signing_key = hashlib.sha256(b"lifevault-sharing-" + (vault._key or b"")).digest()
        _shares = ShareManager(vault._get_conn(), signing_key)
        _shares.initialize()  # Creates shares table if not exists
    return _shares


# ---------------------------------------------------------------------------
# Vault lifecycle tools
# ---------------------------------------------------------------------------

@mcp.tool()
def vault_initialize(passphrase: str) -> str:
    """
    Initialize a new LifeVault with the given passphrase.
    Call this ONCE when setting up the vault for the first time.
    The passphrase is used to derive the AES-256 encryption key.
    It is never stored — only a verification token is saved.

    Args:
        passphrase: A strong passphrase to protect the vault
    """
    result = vault.initialize(passphrase)
    audit = _ensure_audit()
    audit.log("initialize", details="New vault created")
    return result


@mcp.tool()
def vault_unlock(passphrase: str) -> str:
    """
    Unlock an existing vault with your passphrase.
    Must be called before any document operations.

    Args:
        passphrase: The passphrase used when the vault was created
    """
    try:
        result = vault.unlock(passphrase)
        audit = _ensure_audit()
        audit.log("unlock", details="Vault unlocked successfully")
        return result
    except ValueError as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool()
def vault_stats() -> str:
    """
    Get vault statistics: total documents, categories breakdown,
    and active deadlines count.
    """
    try:
        stats = vault.get_vault_stats()
        audit = _ensure_audit()
        audit.log("stats", details="Viewed vault statistics")
        return json.dumps(stats, indent=2)
    except RuntimeError as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Document CRUD tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def store_document(
    category: str,
    title: str,
    extracted_data: str,
    raw_text: str,
    file_type: str = "text",
) -> str:
    """
    Store a new document in the encrypted vault.
    The content is encrypted with AES-256-GCM before storage.

    Args:
        category: Document type — one of: insurance, medical, legal,
                  financial, warranty, subscription, identity, other
        title: Human-readable title (e.g., "Car Insurance - State Farm 2025")
        extracted_data: JSON string of structured data extracted from the document
        raw_text: The original text content of the document
        file_type: Source format — pdf, image, text
    """
    try:
        # Parse extracted_data from JSON string
        data_dict = json.loads(extracted_data) if isinstance(extracted_data, str) else extracted_data

        # Generate semantic embedding for search
        search_text = f"{title} {category} {raw_text[:2000]}"
        embedding = await generate_embedding(search_text)

        # Store encrypted
        doc_id = vault.store_document(
            category=category,
            title=title,
            extracted_data=data_dict,
            raw_text=raw_text,
            file_type=file_type,
            embedding=embedding,
        )

        audit = _ensure_audit()
        audit.log("store", doc_id=doc_id, details=f"Stored {category}: {title}")

        return json.dumps({
            "status": "success",
            "doc_id": doc_id,
            "message": f"Document '{title}' encrypted and stored successfully.",
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_document(doc_id: str) -> str:
    """
    Retrieve and decrypt a document by its ID.

    Args:
        doc_id: The UUID of the document to retrieve
    """
    try:
        doc = vault.get_document(doc_id)
        if doc is None:
            return json.dumps({"status": "error", "message": "Document not found."})

        audit = _ensure_audit()
        audit.log("read", doc_id=doc_id, details=f"Read document: {doc.title}")

        return json.dumps({
            "status": "success",
            "document": {
                "doc_id": doc.doc_id,
                "category": doc.category,
                "title": doc.title,
                "extracted_data": doc.extracted_data,
                "raw_text": doc.raw_text,
                "file_type": doc.file_type,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
            }
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
async def search_vault(query: str, category: str = "", limit: int = 5) -> str:
    """
    Search the vault using natural language (semantic search).
    Finds documents most relevant to your query.

    Args:
        query: Natural language search query (e.g., "car insurance policy")
        category: Optional category filter (leave empty for all)
        limit: Maximum number of results (default 5)
    """
    try:
        # Generate query embedding
        query_emb = await generate_query_embedding(query)

        # Get all stored embeddings (decrypted in-memory)
        stored_embeddings = vault.get_all_embeddings()

        # Compute similarity
        results = search_embeddings(query_emb, stored_embeddings, top_k=limit)

        if not results:
            audit = _ensure_audit()
            audit.log("search", details=f"Search '{query}' — no results")
            return json.dumps({"status": "success", "results": [], "message": "No matching documents found."})

        # Fetch document summaries for results
        matched_docs = []
        for doc_id, score in results:
            doc = vault.get_document(doc_id)
            if doc and (not category or doc.category == category):
                matched_docs.append({
                    "doc_id": doc.doc_id,
                    "category": doc.category,
                    "title": doc.title,
                    "relevance_score": round(score, 3),
                    "created_at": doc.created_at,
                    # Include a preview of extracted data
                    "preview": {k: v for k, v in list(doc.extracted_data.items())[:5]},
                })

        audit = _ensure_audit()
        audit.log("search", details=f"Search '{query}' — {len(matched_docs)} results")

        return json.dumps({"status": "success", "results": matched_docs}, indent=2)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def list_documents(category: str = "") -> str:
    """
    List all documents in the vault (without decrypting content).
    Optionally filter by category.

    Args:
        category: Filter by category (leave empty for all).
                  Options: insurance, medical, legal, financial,
                  warranty, subscription, identity, other
    """
    try:
        docs = vault.list_documents(category=category if category else None)

        audit = _ensure_audit()
        audit.log("list", details=f"Listed documents (category={category or 'all'})")

        return json.dumps({
            "status": "success",
            "count": len(docs),
            "documents": [
                {
                    "doc_id": d.doc_id,
                    "category": d.category,
                    "title": d.title,
                    "file_type": d.file_type,
                    "created_at": d.created_at,
                    "updated_at": d.updated_at,
                }
                for d in docs
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
async def update_document(
    doc_id: str,
    category: str = "",
    title: str = "",
    extracted_data: str = "",
    raw_text: str = "",
) -> str:
    """
    Update an existing document in the vault.
    Only fields you provide will be changed; omit fields to keep them unchanged.
    Completes the CRUD lifecycle (Create, Read, Update, Delete).

    Args:
        doc_id: The UUID of the document to update
        category: New category (leave empty to keep current)
        title: New title (leave empty to keep current)
        extracted_data: New structured data as JSON string (leave empty to keep current)
        raw_text: New raw text content (leave empty to keep current)
    """
    try:
        # Build kwargs — only pass fields that were actually provided
        kwargs = {}
        if category:
            kwargs["category"] = category
        if title:
            kwargs["title"] = title
        if extracted_data:
            kwargs["extracted_data"] = (
                json.loads(extracted_data) if isinstance(extracted_data, str)
                else extracted_data
            )
        if raw_text:
            kwargs["raw_text"] = raw_text
            # Re-generate embedding when content changes
            search_text = f"{title or ''} {category or ''} {raw_text[:2000]}"
            kwargs["embedding"] = await generate_embedding(search_text)

        if not kwargs:
            return json.dumps({"status": "error", "message": "No fields to update. Provide at least one."})

        success = vault.update_document(doc_id, **kwargs)

        audit = _ensure_audit()
        if success:
            audit.log("update", doc_id=doc_id,
                      details=f"Updated fields: {', '.join(kwargs.keys())}")
            return json.dumps({
                "status": "success",
                "doc_id": doc_id,
                "updated_fields": list(kwargs.keys()),
                "message": f"Document updated successfully.",
            })
        else:
            return json.dumps({"status": "error", "message": "Document not found."})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def delete_document(doc_id: str) -> str:
    """
    Permanently delete a document from the vault.
    This also removes associated deadlines.

    Args:
        doc_id: The UUID of the document to delete
    """
    try:
        # Get title before deletion for audit log
        doc = vault.get_document(doc_id)
        title = doc.title if doc else "unknown"

        success = vault.delete_document(doc_id)

        audit = _ensure_audit()
        if success:
            audit.log("delete", doc_id=doc_id, details=f"Deleted document: {title}")
            return json.dumps({"status": "success", "message": f"Document '{title}' deleted."})
        else:
            return json.dumps({"status": "error", "message": "Document not found."})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
# DEADLINE TOOLS
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def add_deadline(
    doc_id: str,
    description: str,
    deadline_date: str,
    alert_days_before: int = 30,
) -> str:
    """
    Add a deadline/reminder linked to a document.

    Args:
        doc_id: The document this deadline relates to
        description: What this deadline is for (e.g., "Policy renewal due")
        deadline_date: Date in YYYY-MM-DD format
        alert_days_before: Days before the deadline to start alerting (default 30)
    """
    try:
        deadline_id = vault.add_deadline(doc_id, description, deadline_date, alert_days_before)

        audit = _ensure_audit()
        audit.log("add_deadline", doc_id=doc_id,
                  details=f"Deadline: {description} on {deadline_date}")

        return json.dumps({
            "status": "success",
            "deadline_id": deadline_id,
            "message": f"Deadline '{description}' set for {deadline_date}.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def list_deadlines(days_ahead: int = 90) -> str:
    """
    List all upcoming deadlines within the specified time window.

    Args:
        days_ahead: Look-ahead window in days (default 90)
    """
    try:
        deadlines = vault.get_upcoming_deadlines(days_ahead)

        audit = _ensure_audit()
        audit.log("list_deadlines", details=f"Checked deadlines ({days_ahead} days ahead)")

        return json.dumps({
            "status": "success",
            "count": len(deadlines),
            "deadlines": [
                {
                    "deadline_id": d.deadline_id,
                    "doc_id": d.doc_id,
                    "description": d.description,
                    "deadline_date": d.deadline_date,
                    "alert_days_before": d.alert_days_before,
                    "status": d.status,
                }
                for d in deadlines
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
# SHARING TOOLS
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def create_share(
    doc_ids: str,
    scope: str,
    recipient_label: str,
    expires_in_hours: int = 24,
) -> str:
    """
    Create a time-limited, scoped share of vault documents.
    Generates a shareable token that auto-expires.

    Args:
        doc_ids: Comma-separated document IDs to include
        scope: Level of detail — "full", "summary", or "emergency"
        recipient_label: Who this is for (e.g., "Dr. Smith")
        expires_in_hours: Hours until expiration (default 24)
    """
    try:
        shares = _ensure_shares()
        id_list = [x.strip() for x in doc_ids.split(",") if x.strip()]

        # Build share data based on scope
        share_data = {}
        for did in id_list:
            doc = vault.get_document(did)
            if doc:
                if scope == "full":
                    share_data[did] = {
                        "title": doc.title,
                        "category": doc.category,
                        "extracted_data": doc.extracted_data,
                    }
                elif scope == "summary":
                    share_data[did] = {
                        "title": doc.title,
                        "category": doc.category,
                        "summary": {k: v for k, v in list(doc.extracted_data.items())[:5]},
                    }

        share = shares.create_share(
            doc_ids=id_list,
            scope=scope,
            share_data=share_data,
            recipient_label=recipient_label,
            expires_in_hours=expires_in_hours,
        )

        audit = _ensure_audit()
        audit.log("create_share",
                  details=f"Shared {len(id_list)} docs with {recipient_label} ({scope}, {expires_in_hours}h)")

        return json.dumps({
            "status": "success",
            "share_id": share.share_id,
            "recipient": recipient_label,
            "expires_at": share.expires_at,
            "message": f"Share created for {recipient_label}. Expires in {expires_in_hours} hours.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def generate_share_qr(share_id: str) -> str:
    """
    Generate a QR code for an existing share.
    Returns the QR code as a base64-encoded PNG.

    Args:
        share_id: The share to generate a QR code for
    """
    try:
        shares = _ensure_shares()
        qr_b64 = shares.generate_qr_code(share_id)

        if qr_b64 is None:
            return json.dumps({"status": "error", "message": "Share not found or expired."})

        audit = _ensure_audit()
        audit.log("generate_qr", details=f"Generated QR for share {share_id}")

        return json.dumps({
            "status": "success",
            "share_id": share_id,
            "qr_code_base64": qr_b64,
            "message": "QR code generated. The recipient can scan this to view the shared data.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def generate_emergency_card(
    allergies: str = "",
    conditions: str = "",
    medications: str = "",
    blood_type: str = "",
    emergency_contacts: str = "",
    expires_in_days: int = 30,
) -> str:
    """
    Generate an emergency medical card as a QR code.
    This creates a time-limited share containing critical health info
    that emergency responders can scan.

    Args:
        allergies: Comma-separated list of allergies
        conditions: Comma-separated list of medical conditions
        medications: Comma-separated list of current medications
        blood_type: Blood type (e.g., "O+", "A-")
        emergency_contacts: Comma-separated contacts (e.g., "Mom: 555-1234, Dad: 555-5678")
        expires_in_days: Card validity in days (default 30)
    """
    try:
        shares = _ensure_shares()

        card_data = {
            "type": "EMERGENCY_MEDICAL_CARD",
            "allergies": [a.strip() for a in allergies.split(",") if a.strip()],
            "conditions": [c.strip() for c in conditions.split(",") if c.strip()],
            "medications": [m.strip() for m in medications.split(",") if m.strip()],
            "blood_type": blood_type,
            "emergency_contacts": [c.strip() for c in emergency_contacts.split(",") if c.strip()],
        }

        share, qr_b64 = shares.generate_emergency_card(
            data=card_data,
            expires_in_hours=expires_in_days * 24,
        )

        audit = _ensure_audit()
        audit.log("emergency_card", details=f"Generated emergency medical card (expires in {expires_in_days} days)")

        return json.dumps({
            "status": "success",
            "share_id": share.share_id,
            "expires_at": share.expires_at,
            "qr_code_base64": qr_b64,
            "card_data": card_data,
            "message": f"Emergency medical card generated. Valid for {expires_in_days} days.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def revoke_share(share_id: str) -> str:
    """
    Immediately revoke a share, making it inaccessible.

    Args:
        share_id: The share to revoke
    """
    try:
        shares = _ensure_shares()
        success = shares.revoke_share(share_id)

        audit = _ensure_audit()
        audit.log("revoke_share", details=f"Revoked share {share_id}")

        if success:
            return json.dumps({"status": "success", "message": "Share revoked."})
        else:
            return json.dumps({"status": "error", "message": "Share not found."})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def list_active_shares() -> str:
    """List all currently active (non-expired, non-revoked) shares."""
    try:
        shares = _ensure_shares()
        active = shares.list_active_shares()

        audit = _ensure_audit()
        audit.log("list_shares", details=f"Listed {len(active)} active shares")

        return json.dumps({
            "status": "success",
            "count": len(active),
            "shares": [
                {
                    "share_id": s.share_id,
                    "doc_ids": s.doc_ids,
                    "scope": s.scope,
                    "recipient": s.recipient_label,
                    "created_at": s.created_at,
                    "expires_at": s.expires_at,
                }
                for s in active
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT TOOLS
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_audit_log(limit: int = 20, action_filter: str = "") -> str:
    """
    View the vault's audit log. Every operation is recorded.

    Args:
        limit: Maximum entries to return (default 20, most recent first)
        action_filter: Filter by action type (e.g., "store", "read", "share").
                      Leave empty for all actions.
    """
    try:
        audit = _ensure_audit()
        entries = audit.get_log(
            limit=limit,
            action_filter=action_filter if action_filter else None,
        )

        return json.dumps({
            "status": "success",
            "count": len(entries),
            "entries": [
                {
                    "log_id": e.log_id,
                    "timestamp": e.timestamp,
                    "action": e.action,
                    "doc_id": e.doc_id,
                    "details": e.details,
                }
                for e in entries
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def get_audit_stats() -> str:
    """Get audit log statistics: total events and breakdown by action type."""
    try:
        audit = _ensure_audit()
        stats = audit.get_stats()
        return json.dumps({"status": "success", **stats}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION TOOLS — Quality gates for the extraction LoopAgent
# ═══════════════════════════════════════════════════════════════════════════

# Required fields per document category — used by the validation tool
# to enforce data quality before storage. This acts as the "reviewer's
# checklist" in the LoopAgent extraction-validation pattern.
CATEGORY_REQUIRED_FIELDS = {
    "insurance": ["provider", "policy_number", "coverage_type"],
    "medical": ["provider", "visit_date"],
    "legal": ["parties", "effective_date"],
    "financial": ["institution", "account_type"],
    "warranty": ["product", "warranty_expiry"],
    "subscription": ["service", "billing_cycle"],
    "identity": ["document_type", "issuing_authority"],
    "other": [],
}


@mcp.tool()
def validate_extraction(category: str, extracted_data: str) -> str:
    """
    Validate that extracted document data meets quality standards.
    Used by the LoopAgent reviewer to enforce data completeness
    before a document is stored in the vault.

    Returns a validation report with pass/fail status, missing fields,
    and a quality score. The reviewer agent uses this to decide whether
    to approve storage or request re-extraction.

    Args:
        category: Document category (insurance, medical, legal, etc.)
        extracted_data: JSON string of extracted fields to validate
    """
    try:
        data = json.loads(extracted_data) if isinstance(extracted_data, str) else extracted_data

        required = CATEGORY_REQUIRED_FIELDS.get(category, [])
        missing = [f for f in required if f not in data or not data[f]]
        total_fields = len(data)
        has_dates = any("date" in k.lower() or "expir" in k.lower() for k in data)
        has_amounts = any(
            isinstance(v, (int, float)) or
            (isinstance(v, str) and "$" in v)
            for v in data.values()
        )

        # Quality score: 0-100 based on completeness and richness
        score = min(100, (total_fields * 10) + (20 if has_dates else 0) + (20 if has_amounts else 0))
        score -= len(missing) * 15  # Penalize missing required fields
        score = max(0, score)

        passed = len(missing) == 0 and score >= 50

        return json.dumps({
            "status": "success",
            "validation": {
                "passed": passed,
                "quality_score": score,
                "total_fields_extracted": total_fields,
                "missing_required_fields": missing,
                "has_date_fields": has_dates,
                "has_monetary_fields": has_amounts,
                "recommendation": "APPROVED — ready for storage" if passed
                    else f"NEEDS IMPROVEMENT — missing: {', '.join(missing)}" if missing
                    else "NEEDS IMPROVEMENT — extract more fields for better quality",
            }
        }, indent=2)
    except json.JSONDecodeError:
        return json.dumps({
            "status": "error",
            "message": "extracted_data is not valid JSON. Re-extract as a JSON object.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def check_urgent_deadlines(days_ahead: int = 7) -> str:
    """
    Proactively check for urgent deadlines approaching soon.
    Used by the orchestrator's before_agent_callback to alert
    the user about critical upcoming deadlines at the start of
    every interaction — a proactive concierge behavior.

    Args:
        days_ahead: Look-ahead window in days (default 7 for urgent)
    """
    try:
        deadlines = vault.get_upcoming_deadlines(days_ahead)

        if not deadlines:
            return json.dumps({
                "status": "success",
                "urgent_count": 0,
                "message": "No urgent deadlines. All clear!",
            })

        urgent_list = []
        for d in deadlines:
            # Look up the parent document title
            doc = vault.get_document(d.doc_id)
            doc_title = doc.title if doc else "Unknown document"
            urgent_list.append({
                "deadline_id": d.deadline_id,
                "description": d.description,
                "deadline_date": d.deadline_date,
                "document_title": doc_title,
            })

        return json.dumps({
            "status": "success",
            "urgent_count": len(urgent_list),
            "deadlines": urgent_list,
            "message": f"⚠️ {len(urgent_list)} deadline(s) in the next {days_ahead} days!",
        }, indent=2)
    except RuntimeError:
        # Vault is locked — can't check deadlines yet, not an error
        return json.dumps({
            "status": "success",
            "urgent_count": 0,
            "message": "Vault is locked. Unlock to check deadlines.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
