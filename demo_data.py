#!/usr/bin/env python3
"""
LifeVault Demo Data Loader
============================
Pre-populates the vault with realistic sample documents for
a reliable video demo. Run this before recording.

Usage:
    python demo_data.py [--passphrase MySecureVault2026!]

This script:
  1. Initializes the vault (or unlocks if already initialized)
  2. Stores 5 sample documents across different categories
  3. Adds deadlines for each document
  4. Creates a sample share and emergency card
  5. Generates a few audit log entries

After running, the vault is ready for a full demo walkthrough.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent
load_dotenv(_project_root / ".env")

from mcp_server.storage import VaultStorage
from mcp_server.audit import AuditLogger
from mcp_server.sharing import ShareManager
import hashlib

VAULT_DB_PATH = os.getenv("VAULT_DB_PATH", str(_project_root / "vault.db"))
DEFAULT_PASSPHRASE = "MySecureVault2026!"


# ---------------------------------------------------------------------------
# Sample documents — realistic personal documents across categories
# ---------------------------------------------------------------------------

SAMPLE_DOCS = [
    {
        "category": "insurance",
        "title": "State Farm Auto Insurance - 2026",
        "extracted_data": {
            "provider": "State Farm",
            "policy_number": "SF-2026-789456",
            "policyholder": "Yash Kawade",
            "coverage_type": "Collision + Comprehensive",
            "premium_annual": "$1,200",
            "deductible": "$500",
            "coverage_start": "2026-01-01",
            "coverage_end": "2026-12-31",
            "vehicle": "2024 Honda Civic",
        },
        "raw_text": (
            "State Farm Auto Insurance Policy\n"
            "Policy Number: SF-2026-789456\n"
            "Policyholder: Yash Kawade\n"
            "Vehicle: 2024 Honda Civic\n"
            "Coverage: Collision and Comprehensive\n"
            "Premium: $1,200/year ($100/month)\n"
            "Deductible: $500\n"
            "Coverage Period: January 1, 2026 to December 31, 2026\n"
            "Roadside Assistance: Included\n"
            "Rental Reimbursement: $50/day up to 30 days"
        ),
        "deadline": {
            "description": "Auto insurance renewal due",
            "date": (datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d"),
        },
    },
    {
        "category": "medical",
        "title": "Annual Physical - Dr. Patel 2026",
        "extracted_data": {
            "provider": "Dr. Anita Patel, MD",
            "facility": "Valley Medical Center",
            "visit_date": "2026-05-15",
            "blood_pressure": "118/76",
            "cholesterol_total": "185 mg/dL",
            "blood_glucose_fasting": "92 mg/dL",
            "BMI": "23.4",
            "next_appointment": "2027-05-15",
            "vaccinations_due": "Flu shot (October 2026)",
            "prescriptions": "None currently",
        },
        "raw_text": (
            "Annual Physical Examination Report\n"
            "Patient: Yash Kawade | DOB: 1998-XX-XX\n"
            "Provider: Dr. Anita Patel, MD — Valley Medical Center\n"
            "Date of Visit: May 15, 2026\n\n"
            "Vitals: BP 118/76, HR 72, Temp 98.6F, Weight 165 lbs\n"
            "Blood Panel: Cholesterol 185 (normal), Fasting Glucose 92 (normal)\n"
            "BMI: 23.4 (healthy range)\n\n"
            "Assessment: Patient is in excellent health. No concerns.\n"
            "Recommendations: Annual flu shot in October. Return in 12 months.\n"
            "Allergies: Penicillin (rash), Peanuts (anaphylaxis)"
        ),
        "deadline": {
            "description": "Schedule flu shot",
            "date": "2026-10-01",
        },
    },
    {
        "category": "financial",
        "title": "Chase Savings Account Statement - Q2 2026",
        "extracted_data": {
            "bank": "JPMorgan Chase",
            "account_type": "High Yield Savings",
            "account_last_4": "7823",
            "statement_period": "April 1 - June 30, 2026",
            "opening_balance": "$12,450.00",
            "deposits": "$3,600.00",
            "withdrawals": "$1,200.00",
            "interest_earned": "$89.32",
            "closing_balance": "$14,939.32",
            "apy": "4.25%",
        },
        "raw_text": (
            "JPMorgan Chase — Quarterly Statement\n"
            "High Yield Savings Account ****7823\n"
            "Statement Period: April 1 - June 30, 2026\n\n"
            "Opening Balance: $12,450.00\n"
            "Total Deposits: $3,600.00 (3 deposits)\n"
            "Total Withdrawals: $1,200.00 (1 withdrawal)\n"
            "Interest Earned: $89.32\n"
            "Closing Balance: $14,939.32\n"
            "Current APY: 4.25%\n"
        ),
        "deadline": None,
    },
    {
        "category": "warranty",
        "title": "MacBook Pro 16\" AppleCare+ Warranty",
        "extracted_data": {
            "product": "MacBook Pro 16-inch (M4 Pro)",
            "serial_number": "C02X1234HKJD",
            "purchase_date": "2025-11-20",
            "warranty_type": "AppleCare+",
            "warranty_expiry": "2028-11-20",
            "coverage": "Hardware repairs, accidental damage (2 incidents), battery service",
            "deductible_screen": "$99",
            "deductible_other": "$299",
        },
        "raw_text": (
            "AppleCare+ Protection Plan\n"
            "Product: MacBook Pro 16-inch (M4 Pro, 36GB, 1TB)\n"
            "Serial: C02X1234HKJD\n"
            "Purchased: November 20, 2025\n"
            "AppleCare+ Expires: November 20, 2028\n\n"
            "Coverage includes:\n"
            "- Hardware repairs and replacements\n"
            "- Up to 2 accidental damage incidents per year\n"
            "  - Screen/glass: $99 deductible\n"
            "  - Other damage: $299 deductible\n"
            "- Battery service (if below 80% capacity)\n"
            "- 24/7 priority tech support"
        ),
        "deadline": {
            "description": "AppleCare+ warranty expiration",
            "date": "2028-11-20",
        },
    },
    {
        "category": "legal",
        "title": "Apartment Lease Agreement - 2026-2027",
        "extracted_data": {
            "landlord": "Sunrise Property Management LLC",
            "property_address": "456 Oak Avenue, Apt 12B, San Jose, CA 95112",
            "tenant": "Yash Kawade",
            "lease_start": "2026-03-01",
            "lease_end": "2027-02-28",
            "monthly_rent": "$2,400",
            "security_deposit": "$2,400",
            "pet_deposit": "N/A",
            "late_fee": "$100 after 5th of month",
            "notice_period": "60 days",
        },
        "raw_text": (
            "RESIDENTIAL LEASE AGREEMENT\n"
            "Landlord: Sunrise Property Management LLC\n"
            "Tenant: Yash Kawade\n"
            "Property: 456 Oak Avenue, Apartment 12B, San Jose, CA 95112\n\n"
            "Term: March 1, 2026 through February 28, 2027 (12 months)\n"
            "Monthly Rent: $2,400 due on the 1st of each month\n"
            "Security Deposit: $2,400 (refundable)\n"
            "Late Fee: $100 if paid after the 5th\n"
            "Notice to Vacate: 60 days written notice required\n\n"
            "Utilities included: Water, Trash\n"
            "Tenant responsible for: Electricity, Gas, Internet"
        ),
        "deadline": {
            "description": "Lease renewal — 60-day notice deadline",
            "date": "2026-12-30",
        },
    },
]


async def load_demo_data(passphrase: str = DEFAULT_PASSPHRASE):
    """Load all sample documents into the vault."""
    vault = VaultStorage(VAULT_DB_PATH)

    # Initialize or unlock
    if not vault.is_initialized():
        print(f"Initializing vault at {VAULT_DB_PATH}...")
        vault.initialize(passphrase)
    else:
        print("Vault already initialized. Unlocking...")
        vault.unlock(passphrase)

    # Set up audit logger
    audit = AuditLogger(vault._get_conn())
    audit.initialize()

    # Optionally generate embeddings (requires API key)
    use_embeddings = bool(os.getenv("GOOGLE_API_KEY"))
    if use_embeddings:
        from mcp_server.embeddings import generate_embedding
        print("API key found — will generate search embeddings.\n")
    else:
        print("No API key — skipping embeddings (search won't work).\n")

    doc_ids = []
    for i, doc_info in enumerate(SAMPLE_DOCS, 1):
        print(f"[{i}/{len(SAMPLE_DOCS)}] Storing: {doc_info['title']}...")

        # Generate embedding if possible
        embedding = None
        if use_embeddings:
            try:
                search_text = f"{doc_info['title']} {doc_info['category']} {doc_info['raw_text'][:2000]}"
                embedding = await generate_embedding(search_text)
                print(f"         Embedding generated.")
            except Exception as e:
                print(f"         Embedding failed: {e}")

        doc_id = vault.store_document(
            category=doc_info["category"],
            title=doc_info["title"],
            extracted_data=doc_info["extracted_data"],
            raw_text=doc_info["raw_text"],
            embedding=embedding,
        )
        doc_ids.append(doc_id)

        audit.log("store", doc_id=doc_id,
                  details=f"Demo: Stored {doc_info['category']}: {doc_info['title']}")

        # Add deadline if specified
        if doc_info.get("deadline"):
            dl = doc_info["deadline"]
            dl_id = vault.add_deadline(doc_id, dl["description"], dl["date"])
            audit.log("add_deadline", doc_id=doc_id,
                      details=f"Demo: {dl['description']} on {dl['date']}")
            print(f"         Deadline added: {dl['description']}")

        print(f"         ID: {doc_id}")

    # Create a sample share
    print("\nCreating sample share...")
    signing_key = hashlib.sha256(b"lifevault-sharing-" + vault._key).digest()
    shares = ShareManager(vault._get_conn(), signing_key)
    shares.initialize()

    share = shares.create_share(
        doc_ids=[doc_ids[1]],  # Share the medical record
        scope="summary",
        share_data={
            doc_ids[1]: {
                "title": SAMPLE_DOCS[1]["title"],
                "category": "medical",
                "summary": {k: v for k, v in list(SAMPLE_DOCS[1]["extracted_data"].items())[:5]},
            }
        },
        recipient_label="Dr. Patel",
        expires_in_hours=48,
    )
    audit.log("create_share",
              details=f"Demo: Shared medical record with Dr. Patel (48h)")
    print(f"  Share created: {share.share_id} (for Dr. Patel, 48h)")

    # Summary
    stats = vault.get_vault_stats()
    print(f"\n{'='*50}")
    print(f"Demo data loaded successfully!")
    print(f"  Documents:  {stats['total_documents']}")
    print(f"  Deadlines:  {stats['active_deadlines']}")
    print(f"  Categories: {', '.join(stats['categories'].keys())}")
    print(f"  Passphrase: {passphrase}")
    print(f"{'='*50}")


if __name__ == "__main__":
    pp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PASSPHRASE
    asyncio.run(load_demo_data(pp))
