#!/usr/bin/env python3
"""
LifeVault CLI — Command-Line Interface
========================================
Demonstrates the "Agent Skills / CLI" course concept.

A standalone CLI that talks directly to the LifeVault MCP server
components (no LLM needed), giving users fast, scriptable access
to all vault operations.

Usage:
    python cli.py init
    python cli.py unlock
    python cli.py store --category insurance --title "Car Insurance" --text "..."
    python cli.py search "car insurance"
    python cli.py list [--category medical]
    python cli.py get <doc_id>
    python cli.py update <doc_id> --title "New Title"
    python cli.py delete <doc_id>
    python cli.py deadlines [--days 90]
    python cli.py add-deadline <doc_id> --desc "Renewal due" --date 2026-12-31
    python cli.py share <doc_ids> --scope full --recipient "Dr. Smith"
    python cli.py emergency-card --allergies "penicillin" --blood-type "O+"
    python cli.py audit [--limit 20]
    python cli.py stats
"""

import argparse
import asyncio
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup: load .env and configure paths
# ---------------------------------------------------------------------------

_project_root = Path(__file__).resolve().parent
load_dotenv(_project_root / ".env")

# Import LifeVault components (direct access — no MCP round-trip)
from mcp_server.storage import VaultStorage
from mcp_server.audit import AuditLogger
from mcp_server.sharing import ShareManager
from mcp_server.crypto import derive_key
import hashlib

VAULT_DB_PATH = os.getenv("VAULT_DB_PATH", str(_project_root / "vault.db"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_vault() -> VaultStorage:
    """Create a VaultStorage instance pointed at the configured DB."""
    return VaultStorage(VAULT_DB_PATH)


def _unlock_vault(vault: VaultStorage, passphrase: str = None) -> None:
    """Unlock the vault, prompting for passphrase if not provided."""
    if not vault.is_initialized():
        print("Error: Vault is not initialized. Run: python cli.py init")
        sys.exit(1)
    if passphrase is None:
        passphrase = getpass.getpass("Vault passphrase: ")
    try:
        vault.unlock(passphrase)
    except ValueError:
        print("Error: Incorrect passphrase.")
        sys.exit(1)


def _get_audit(vault: VaultStorage) -> AuditLogger:
    """Create an audit logger using the vault's connection."""
    audit = AuditLogger(vault._get_conn())
    audit.initialize()
    return audit


def _get_shares(vault: VaultStorage) -> ShareManager:
    """Create a share manager with a signing key derived from the vault key."""
    signing_key = hashlib.sha256(
        b"lifevault-sharing-" + (vault._key or b"")
    ).digest()
    mgr = ShareManager(vault._get_conn(), signing_key)
    mgr.initialize()
    return mgr


def _print_json(data: dict) -> None:
    """Pretty-print a JSON-serializable dict."""
    print(json.dumps(data, indent=2, default=str))


def _open_file(path: Path) -> None:
    """Open a file with the system's default application if possible."""
    if not path.exists():
        return

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            return
    except Exception as exc:
        print(f"  Warning: Could not open QR code automatically ({exc}).")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args):
    """Initialize a new vault."""
    vault = _get_vault()
    if vault.is_initialized():
        print("Vault is already initialized. Use 'unlock' instead.")
        return
    if args.passphrase:
        passphrase = args.passphrase
    else:
        passphrase = getpass.getpass("Choose a vault passphrase: ")
        confirm = getpass.getpass("Confirm passphrase: ")
        if passphrase != confirm:
            print("Error: Passphrases do not match.")
            sys.exit(1)
    result = vault.initialize(passphrase)
    print(f"Success: {result}")
    print("Important: Remember your passphrase — it cannot be recovered!")


def cmd_unlock(args):
    """Test unlocking the vault (verify passphrase works)."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)
    print("Vault unlocked successfully.")


def cmd_store(args):
    """Store a new document in the vault."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    # Build extracted_data from the raw text (simple key-value for CLI usage)
    extracted = {}
    if args.extracted_data:
        try:
            extracted = json.loads(args.extracted_data)
        except json.JSONDecodeError:
            print("Warning: --extracted-data is not valid JSON, storing as-is.")
            extracted = {"raw_input": args.extracted_data}
    else:
        # Auto-extract basic info from text for convenience
        extracted = {"content_preview": args.text[:200] if args.text else ""}

    # Optionally generate embedding for semantic search
    embedding = None
    if not args.no_embedding:
        try:
            from mcp_server.embeddings import generate_embedding
            search_text = f"{args.title} {args.category} {args.text[:2000]}"
            embedding = asyncio.run(generate_embedding(search_text))
            print("  Embedding generated for semantic search.")
        except Exception as e:
            print(f"  Warning: Could not generate embedding ({e}). Storing without.")

    doc_id = vault.store_document(
        category=args.category,
        title=args.title,
        extracted_data=extracted,
        raw_text=args.text,
        file_type=args.file_type,
        embedding=embedding,
    )

    # Log the action
    audit = _get_audit(vault)
    audit.log("store", doc_id=doc_id, details=f"CLI: Stored {args.category}: {args.title}")

    print(f"Document stored successfully.")
    print(f"  ID:       {doc_id}")
    print(f"  Title:    {args.title}")
    print(f"  Category: {args.category}")


def cmd_search(args):
    """Semantic search across the vault."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    from mcp_server.embeddings import generate_query_embedding, search_embeddings

    # Generate query embedding
    query_emb = asyncio.run(generate_query_embedding(args.query))

    # Get stored embeddings (decrypted in memory)
    stored = vault.get_all_embeddings()
    if not stored:
        print("No documents with embeddings found. Store some documents first.")
        return

    # Compute similarity
    results = search_embeddings(query_emb, stored, top_k=args.limit)
    if not results:
        print(f"No results for: {args.query}")
        return

    print(f"Search results for: \"{args.query}\"\n")
    for doc_id, score in results:
        doc = vault.get_document(doc_id)
        if doc:
            print(f"  [{score:.3f}] {doc.title}")
            print(f"          Category: {doc.category} | ID: {doc.doc_id}")
            print()

    audit = _get_audit(vault)
    audit.log("search", details=f"CLI: Search '{args.query}' — {len(results)} results")


def cmd_list(args):
    """List all documents in the vault."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    docs = vault.list_documents(category=args.category)
    if not docs:
        print("No documents found.")
        return

    print(f"Documents in vault ({len(docs)} total):\n")
    for d in docs:
        print(f"  [{d.category:12s}] {d.title}")
        print(f"                 ID: {d.doc_id} | Created: {d.created_at[:10]}")
        print()


def cmd_get(args):
    """Retrieve and display a document."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    doc = vault.get_document(args.doc_id)
    if not doc:
        print(f"Document not found: {args.doc_id}")
        return

    print(f"Title:    {doc.title}")
    print(f"Category: {doc.category}")
    print(f"Type:     {doc.file_type}")
    print(f"Created:  {doc.created_at}")
    print(f"Updated:  {doc.updated_at}")
    print(f"\nExtracted Data:")
    _print_json(doc.extracted_data)
    print(f"\nRaw Text:\n{doc.raw_text[:1000]}")

    audit = _get_audit(vault)
    audit.log("read", doc_id=args.doc_id, details=f"CLI: Read document: {doc.title}")


def cmd_update(args):
    """Update an existing document's metadata or content."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    # Only pass fields that were explicitly provided
    kwargs = {}
    if args.title:
        kwargs["title"] = args.title
    if args.category:
        kwargs["category"] = args.category
    if args.text:
        kwargs["raw_text"] = args.text
    if args.extracted_data:
        try:
            kwargs["extracted_data"] = json.loads(args.extracted_data)
        except json.JSONDecodeError:
            print("Error: --extracted-data must be valid JSON.")
            sys.exit(1)

    if not kwargs:
        print("Nothing to update. Provide --title, --category, --text, or --extracted-data.")
        return

    success = vault.update_document(args.doc_id, **kwargs)
    if success:
        print(f"Document {args.doc_id} updated successfully.")
        audit = _get_audit(vault)
        audit.log("update", doc_id=args.doc_id,
                  details=f"CLI: Updated fields: {', '.join(kwargs.keys())}")
    else:
        print(f"Document not found: {args.doc_id}")


def cmd_delete(args):
    """Delete a document from the vault."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    # Safety: show doc title before deleting
    doc = vault.get_document(args.doc_id)
    if not doc:
        print(f"Document not found: {args.doc_id}")
        return

    if not args.force:
        confirm = input(f"Delete \"{doc.title}\"? This cannot be undone. [y/N]: ")
        if confirm.lower() != "y":
            print("Cancelled.")
            return

    vault.delete_document(args.doc_id)
    audit = _get_audit(vault)
    audit.log("delete", doc_id=args.doc_id, details=f"CLI: Deleted: {doc.title}")
    print(f"Document \"{doc.title}\" deleted.")


def cmd_deadlines(args):
    """List upcoming deadlines."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    deadlines = vault.get_upcoming_deadlines(days_ahead=args.days)
    if not deadlines:
        print(f"No deadlines in the next {args.days} days.")
        return

    print(f"Upcoming deadlines (next {args.days} days):\n")
    for dl in deadlines:
        print(f"  {dl.deadline_date}  {dl.description}")
        print(f"                 Doc: {dl.doc_id} | Status: {dl.status}")
        print()


def cmd_add_deadline(args):
    """Add a deadline linked to a document."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    dl_id = vault.add_deadline(
        doc_id=args.doc_id,
        description=args.desc,
        deadline_date=args.date,
        alert_days_before=args.alert_days,
    )

    audit = _get_audit(vault)
    audit.log("add_deadline", doc_id=args.doc_id,
              details=f"CLI: Deadline '{args.desc}' on {args.date}")

    print(f"Deadline added: {dl_id}")


def cmd_share(args):
    """Create a time-limited share."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)
    shares = _get_shares(vault)

    doc_ids = [d.strip() for d in args.doc_ids.split(",")]

    # Build share data based on scope
    share_data = {}
    for did in doc_ids:
        doc = vault.get_document(did)
        if doc:
            if args.scope == "full":
                share_data[did] = {
                    "title": doc.title,
                    "category": doc.category,
                    "extracted_data": doc.extracted_data,
                }
            elif args.scope == "summary":
                share_data[did] = {
                    "title": doc.title,
                    "category": doc.category,
                    "summary": {k: v for k, v in list(doc.extracted_data.items())[:5]},
                }

    share = shares.create_share(
        doc_ids=doc_ids,
        scope=args.scope,
        share_data=share_data,
        recipient_label=args.recipient,
        expires_in_hours=args.hours,
    )

    audit = _get_audit(vault)
    audit.log("create_share",
              details=f"CLI: Shared with {args.recipient} ({args.scope}, {args.hours}h)")

    print(f"Share created successfully.")
    print(f"  Share ID:  {share.share_id}")
    print(f"  Recipient: {args.recipient}")
    print(f"  Scope:     {args.scope}")
    print(f"  Expires:   {share.expires_at}")


def cmd_generate_share_qr(args):
    """Generate a QR code for an existing share ID."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)
    shares = _get_shares(vault)

    try:
        import base64
        qr_b64 = shares.generate_qr_code(args.share_id)
    except Exception as exc:
        print(f"Error: Could not generate QR code for share {args.share_id}: {exc}")
        sys.exit(1)

    if not qr_b64:
        print(f"Error: No QR code found for share {args.share_id}")
        sys.exit(1)

    qr_bytes = base64.b64decode(qr_b64)
    output_path = Path(args.output)
    output_path.write_bytes(qr_bytes)
    print(f"  QR code saved to: {output_path}")
    _open_file(output_path)


def cmd_emergency_card(args):
    """Generate an emergency medical card."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)
    shares = _get_shares(vault)

    card_data = {
        "type": "EMERGENCY_MEDICAL_CARD",
        "allergies": [a.strip() for a in args.allergies.split(",") if a.strip()] if args.allergies else [],
        "conditions": [c.strip() for c in args.conditions.split(",") if c.strip()] if args.conditions else [],
        "medications": [m.strip() for m in args.medications.split(",") if m.strip()] if args.medications else [],
        "blood_type": args.blood_type or "",
        "emergency_contacts": [c.strip() for c in args.contacts.split(",") if c.strip()] if args.contacts else [],
    }

    share, qr_b64 = shares.generate_emergency_card(
        data=card_data,
        expires_in_hours=args.days * 24,
    )

    # Save QR code to file if requested
    if qr_b64 and args.output:
        import base64
        qr_bytes = base64.b64decode(qr_b64)
        output_path = Path(args.output)
        output_path.write_bytes(qr_bytes)
        print(f"  QR code saved to: {output_path}")
        _open_file(output_path)

    audit = _get_audit(vault)
    audit.log("emergency_card",
              details=f"CLI: Emergency card (expires in {args.days} days)")

    print(f"Emergency medical card generated.")
    print(f"  Share ID: {share.share_id}")
    print(f"  Expires:  {share.expires_at}")
    _print_json(card_data)


def cmd_audit(args):
    """View the audit log."""
    vault = _get_vault()
    # Audit log can be read without full unlock if vault is initialized
    if vault.is_initialized():
        # Still need connection, use a minimal unlock approach
        vault._conn = vault._get_conn()
    audit = _get_audit(vault)

    entries = audit.get_log(
        limit=args.limit,
        action_filter=args.action if args.action else None,
    )
    if not entries:
        print("No audit log entries.")
        return

    print(f"Audit log (last {args.limit} entries):\n")
    for e in entries:
        doc_str = f" [{e.doc_id[:8]}...]" if e.doc_id else ""
        print(f"  {e.timestamp[:19]}  {e.action:15s}{doc_str}  {e.details}")


def cmd_stats(args):
    """Show vault statistics."""
    vault = _get_vault()
    _unlock_vault(vault, args.passphrase)

    stats = vault.get_vault_stats()
    print("Vault Statistics:")
    print(f"  Total documents:   {stats['total_documents']}")
    print(f"  Active deadlines:  {stats['active_deadlines']}")
    print(f"  Categories:")
    for cat, count in stats.get("categories", {}).items():
        print(f"    {cat}: {count}")

    # Also show audit stats
    audit = _get_audit(vault)
    audit_stats = audit.get_stats()
    print(f"\n  Audit log entries: {audit_stats.get('total_events', 0)}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="lifevault",
        description="LifeVault — Secure Personal Life Management CLI",
        epilog="All document content is encrypted with AES-256-GCM. Your passphrase never leaves this device.",
    )
    parser.add_argument(
        "--passphrase", "-p",
        help="Vault passphrase (will prompt if not provided)",
        default=None,
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # --- init ---
    sub.add_parser("init", help="Initialize a new vault")

    # --- unlock ---
    sub.add_parser("unlock", help="Test vault passphrase")

    # --- store ---
    p_store = sub.add_parser("store", help="Store a new document")
    p_store.add_argument("--category", "-c", required=True,
                         choices=["insurance", "medical", "legal", "financial",
                                  "warranty", "subscription", "identity", "other"],
                         help="Document category")
    p_store.add_argument("--title", "-t", required=True, help="Document title")
    p_store.add_argument("--text", required=True, help="Document text content")
    p_store.add_argument("--file-type", default="text", help="Source type (default: text)")
    p_store.add_argument("--extracted-data", default=None,
                         help="JSON string of structured extracted data")
    p_store.add_argument("--no-embedding", action="store_true",
                         help="Skip embedding generation (faster, no search)")

    # --- search ---
    p_search = sub.add_parser("search", help="Semantic search the vault")
    p_search.add_argument("query", help="Natural language search query")
    p_search.add_argument("--limit", type=int, default=5, help="Max results")

    # --- list ---
    p_list = sub.add_parser("list", help="List all documents")
    p_list.add_argument("--category", default=None, help="Filter by category")

    # --- get ---
    p_get = sub.add_parser("get", help="Retrieve a document by ID")
    p_get.add_argument("doc_id", help="Document UUID")

    # --- update ---
    p_update = sub.add_parser("update", help="Update a document")
    p_update.add_argument("doc_id", help="Document UUID to update")
    p_update.add_argument("--title", default=None, help="New title")
    p_update.add_argument("--category", default=None, help="New category")
    p_update.add_argument("--text", default=None, help="New raw text")
    p_update.add_argument("--extracted-data", default=None, help="New extracted data (JSON)")

    # --- delete ---
    p_del = sub.add_parser("delete", help="Delete a document")
    p_del.add_argument("doc_id", help="Document UUID to delete")
    p_del.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # --- deadlines ---
    p_dl = sub.add_parser("deadlines", help="List upcoming deadlines")
    p_dl.add_argument("--days", type=int, default=90, help="Look-ahead days (default: 90)")

    # --- add-deadline ---
    p_adl = sub.add_parser("add-deadline", help="Add a deadline to a document")
    p_adl.add_argument("doc_id", help="Document UUID")
    p_adl.add_argument("--desc", required=True, help="Deadline description")
    p_adl.add_argument("--date", required=True, help="Deadline date (YYYY-MM-DD)")
    p_adl.add_argument("--alert-days", type=int, default=30, help="Alert days before")

    # --- share ---
    p_share = sub.add_parser("share", help="Create a time-limited share")
    p_share.add_argument("doc_ids", help="Comma-separated document IDs")
    p_share.add_argument("--scope", choices=["full", "summary", "emergency"],
                         default="summary", help="Share scope")
    p_share.add_argument("--recipient", required=True, help="Recipient label")
    p_share.add_argument("--hours", type=int, default=24, help="Expiry hours")

    # --- generate-share-qr ---
    p_share_qr = sub.add_parser("generate-share-qr", help="Generate a QR code for an existing share ID")
    p_share_qr.add_argument("--share-id", required=True, help="Existing share ID")
    p_share_qr.add_argument("--output", required=True, help="Output image path for the QR code")

    # --- emergency-card ---
    p_emg = sub.add_parser("emergency-card", help="Generate an emergency medical card")
    p_emg.add_argument("--allergies", default="", help="Comma-separated allergies")
    p_emg.add_argument("--conditions", default="", help="Comma-separated conditions")
    p_emg.add_argument("--medications", default="", help="Comma-separated medications")
    p_emg.add_argument("--blood-type", default="", help="Blood type (e.g., O+)")
    p_emg.add_argument("--contacts", default="", help="Emergency contacts")
    p_emg.add_argument("--days", type=int, default=30, help="Card validity in days")
    p_emg.add_argument("--output", "-o", default=None, help="Save QR code to file")

    # --- audit ---
    p_audit = sub.add_parser("audit", help="View the audit log")
    p_audit.add_argument("--limit", type=int, default=20, help="Max entries")
    p_audit.add_argument("--action", default=None, help="Filter by action type")

    # --- stats ---
    sub.add_parser("stats", help="Show vault statistics")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    "init": cmd_init,
    "unlock": cmd_unlock,
    "store": cmd_store,
    "search": cmd_search,
    "list": cmd_list,
    "get": cmd_get,
    "update": cmd_update,
    "delete": cmd_delete,
    "deadlines": cmd_deadlines,
    "add-deadline": cmd_add_deadline,
    "share": cmd_share,
    "generate-share-qr": cmd_generate_share_qr,
    "emergency-card": cmd_emergency_card,
    "audit": cmd_audit,
    "stats": cmd_stats,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = COMMANDS.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
