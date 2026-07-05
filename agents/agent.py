"""
LifeVault multi-agent entry point.

This module defines the root orchestrator and the specialist agents used for
secure document ingestion, vault management, deadline monitoring, and
controlled sharing. Each agent is provided with a scoped MCP toolset so it
can access only the capabilities required for its role.

Run locally with:
    adk run agents/
    adk web agents/
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------

# Project root is the repository directory, one level above the agents folder.
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

# Load environment variables such as API keys and model overrides.
load_dotenv(Path(PROJECT_ROOT) / ".env")

MODEL = os.getenv("GOOGLE_GENAI_MODEL", "gemini-2.5-flash")

# Each agent runs the MCP server in its own subprocess via stdio transport.
MCP_SERVER_CMD = sys.executable
MCP_SERVER_ARGS = ["-m", "mcp_server.server"]
MCP_SERVER_CWD = PROJECT_ROOT


def _mcp(tool_filter: list[str] | None = None) -> McpToolset:
    """Create an MCP toolset for a specific agent role.

    Each agent receives a scoped toolset so it can access only the tools it
    needs. This keeps responsibilities well separated and limits the surface
    area available to each sub-agent.
    """
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=MCP_SERVER_CMD,
                args=MCP_SERVER_ARGS,
                env={
                    **os.environ,
                    "PYTHONPATH": PROJECT_ROOT,
                },
                cwd=MCP_SERVER_CWD,
            ),
            timeout=30,
        ),
        tool_filter=tool_filter,
    )


# ---------------------------------------------------------------------------
# Document ingestion pipeline
# ---------------------------------------------------------------------------
# The ingestion flow validates an extraction before persisting it to the vault.
# It follows a two-stage workflow: first validate the structured output, then
# store the approved document with any relevant deadlines.

# Phase 1: Extract structured data from the user-provided document text.
doc_extractor = Agent(
    name="document_extractor",
    model=MODEL,
    description="Extracts structured data from raw document text",
    instruction="""You are a document extraction specialist for LifeVault.

Given raw document text from the user, extract ALL structured data:

1. CLASSIFY into a category: insurance, medical, legal, financial,
   warranty, subscription, identity, or other

2. EXTRACT key fields including:
   - Dates (expiry, renewal, start, visit dates)
   - Monetary amounts (premiums, deductibles, balances, costs)
   - Reference numbers (policy numbers, account numbers)
   - Parties (providers, institutions, contacts)
   - Key terms and conditions

3. Generate a clear, descriptive TITLE

Output your extraction as a JSON code block like:
```json
{
  "category": "insurance",
  "title": "State Farm Auto Insurance - 2026",
  "extracted_data": {
    "provider": "State Farm",
    "policy_number": "SF-2026-789456",
    ...
  }
}
```

Be thorough — every field you miss is a field the user can't search for later.
If the reviewer sends you feedback, incorporate it and re-extract.""",
)


# Phase 2: Review the extraction and provide feedback when needed.
doc_reviewer = Agent(
    name="document_reviewer",
    model=MODEL,
    description="Reviews and validates document extraction quality",
    instruction="""You are a quality assurance reviewer for LifeVault extractions.

Your job is to validate the extraction produced by the extractor agent.

STEPS:
1. Read the extraction from the previous message
2. Parse the JSON to get the category and extracted_data
3. Call validate_extraction with the category and extracted_data JSON string
4. Based on the validation result:

   IF validation PASSED (quality_score >= 50, no missing fields):
   → Respond with "EXTRACTION APPROVED" and include the final validated JSON

   IF validation FAILED:
   → List the specific issues (missing fields, low quality score)
   → Provide concrete suggestions for what the extractor should add
   → The loop will send your feedback back to the extractor

Be strict — personal documents need accuracy. But don't loop forever;
if the extraction has the essentials, approve it.""",
    tools=[
        _mcp(tool_filter=["validate_extraction"]),
    ],
)


# Phase 2b: Iterate the extractor/reviewer loop until the extraction is
# approved or the iteration budget is reached.
extraction_loop = LoopAgent(
    name="extraction_quality_loop",
    sub_agents=[doc_extractor, doc_reviewer],
    max_iterations=2,
)


# Phase 3: Persist the approved document and register any deadlines found.
doc_storer = Agent(
    name="document_storer",
    model=MODEL,
    description="Stores validated documents in the encrypted vault",
    instruction="""You are the LifeVault document storage agent.

Read the APPROVED extraction from the conversation above and:

1. Parse the final JSON extraction (category, title, extracted_data)
2. Call store_document with:
   - category: the classified category
   - title: the generated title
   - extracted_data: the structured data as a JSON string
   - raw_text: the original text from the user's first message
   - file_type: "text" (default)
3. If the extracted data contains any important dates (expiry, renewal,
   due dates), call add_deadline for each one
4. Confirm to the user what was stored and any deadlines added

If the vault is locked, call vault_unlock first.
If the vault doesn't exist, call vault_initialize first.""",
    tools=[
        _mcp(tool_filter=[
            "store_document",
            "add_deadline",
            "vault_initialize",
            "vault_unlock",
        ]),
    ],
)


# Compose the ingestion workflow as a sequential pipeline.
document_agent = SequentialAgent(
    name="document_agent",
    description=(
        "Handles document ingestion with quality validation. Uses a "
        "LoopAgent to extract and validate structured data, then stores "
        "the approved document encrypted in the vault. Use this for any "
        "request to store, save, add, or upload a document."
    ),
    sub_agents=[extraction_loop, doc_storer],
)


# ---------------------------------------------------------------------------
# Vault management agent
# ---------------------------------------------------------------------------
# Responsible for read, update, delete, and search operations over stored
# documents, with the broadest MCP tool access of the specialist agents.

vault_agent = Agent(
    name="vault_agent",
    model=MODEL,
    description=(
        "Manages the encrypted document vault: searches for documents, "
        "retrieves document details, lists stored documents, updates or "
        "deletes documents, and shows vault statistics. Use for any "
        "query about finding, viewing, listing, updating, or removing "
        "documents."
    ),
    instruction="""You are the LifeVault Vault Agent.

You manage the encrypted document vault. You can:
- SEARCH for documents using natural language queries (search_vault)
- LIST all documents, optionally filtered by category (list_documents)
- RETRIEVE full document details by ID (get_document)
- UPDATE document metadata or content (update_document)
- DELETE documents (delete_document) — always warn before deleting
- Show vault STATISTICS (vault_stats)

When searching, use descriptive natural language for best results.
The vault uses semantic search (Gemini embeddings + cosine similarity),
so queries like "car insurance" or "medical records" work well.

Always present results clearly:
- For lists: show title, category, and date
- For full documents: show all extracted fields
- For search: show relevance scores

SECURITY: When deleting, always confirm with the user first.
This is an irreversible operation on encrypted data.

If the vault is locked, call vault_unlock first.""",
    tools=[
        _mcp(tool_filter=[
            "search_vault",
            "list_documents",
            "get_document",
            "update_document",
            "delete_document",
            "vault_stats",
            "vault_unlock",
            "vault_initialize",
        ]),
    ],
)


# ---------------------------------------------------------------------------
# Advisory agent
# ---------------------------------------------------------------------------
# Monitors deadlines, surfaces urgent reminders, and summarizes audit activity.

advisory_agent = Agent(
    name="advisory_agent",
    model=MODEL,
    description=(
        "Monitors deadlines, renewals, and expirations. Provides proactive "
        "alerts about upcoming dates and suggests actions the user should "
        "take. Also reviews the audit trail for security insights. Use for "
        "any question about deadlines, reminders, what's expiring, or "
        "audit history."
    ),
    instruction="""You are the LifeVault Advisory Agent — a proactive concierge.

Your job is to help users stay on top of important deadlines:

DEADLINE MANAGEMENT:
- CHECK for urgent deadlines first (check_urgent_deadlines)
- LIST upcoming deadlines within a time window (list_deadlines)
- ADD new deadlines linked to documents (add_deadline)

When presenting deadlines, rank by urgency:
- 🔴 URGENT: ≤7 days — emphasize immediate action needed
- 🟡 WARNING: ≤30 days — suggest planning ahead
- 🟢 OK: >30 days — informational only

For each deadline, suggest SPECIFIC next steps:
- "Your car insurance expires in 14 days — start comparing quotes"
- "Lease renewal notice due in 30 days — review terms and decide"
- "Warranty expiring next month — file any pending claims now"

AUDIT MONITORING:
- Check the AUDIT LOG to see recent vault activity (get_audit_log)
- Get audit STATISTICS to summarize access patterns (get_audit_stats)
- Flag unusual patterns (e.g., many reads of sensitive documents)

Be proactive and helpful. Don't wait to be asked — if deadlines are
close, lead with that information.""",
    tools=[
        _mcp(tool_filter=[
            "list_deadlines",
            "add_deadline",
            "check_urgent_deadlines",
            "get_audit_log",
            "get_audit_stats",
            "vault_unlock",
        ]),
    ],
)


# ---------------------------------------------------------------------------
# Sharing agent
# ---------------------------------------------------------------------------
# Creates time-limited shares and emergency artifacts with an explicit
# confirmation gate before any sharing action is executed.

sharing_agent = Agent(
    name="sharing_agent",
    model=MODEL,
    description=(
        "Handles secure document sharing: creates time-limited shares, "
        "generates QR codes, creates emergency medical cards, revokes "
        "shares, and lists active shares. Use for any request to share "
        "documents, create QR codes, or manage shares."
    ),
    instruction="""You are the LifeVault Sharing Agent.

You manage secure, time-limited document sharing:
- CREATE a share with specific documents, scope, and expiry (create_share)
- GENERATE QR codes for existing shares (generate_share_qr)
- Create EMERGENCY MEDICAL CARDS with allergies, conditions, medications,
  blood type, and emergency contacts (generate_emergency_card)
- REVOKE shares immediately (revoke_share)
- LIST all active shares (list_active_shares)

Sharing scopes:
- "full": All extracted data (for trusted recipients like doctors)
- "summary": Key fields only (for general sharing)
- "emergency": Critical health info only (for first responders)

⚠️ SAFETY PROTOCOL — Before creating ANY share, you MUST:
1. List what documents will be shared
2. State the scope (full/summary/emergency)
3. Name the recipient
4. State the expiry duration
5. Ask the user to CONFIRM before proceeding

This confirmation step is a security gate — sharing encrypted data
requires explicit user consent. Never skip it.

After sharing, remind the user:
- All shares are time-limited and auto-expire
- Shares can be revoked instantly with revoke_share
- QR codes contain the actual data — treat as sensitive
- Emergency cards should be renewed periodically""",
    tools=[
        _mcp(tool_filter=[
            "create_share",
            "generate_share_qr",
            "generate_emergency_card",
            "revoke_share",
            "list_active_shares",
            "get_document",
            "list_documents",
            "vault_unlock",
        ]),
    ],
)


# ---------------------------------------------------------------------------
# Root orchestrator
# ---------------------------------------------------------------------------
# The root agent is the entry point for ADK. It exposes only the high-level
# lifecycle and monitoring tools and delegates domain-specific work to the
# specialist sub-agents.

root_agent = Agent(
    name="lifevault",
    model=MODEL,
    description="LifeVault — Secure Personal Life Management Concierge",
    instruction="""You are LifeVault, a secure personal life management concierge.

You help users manage their important documents, track deadlines,
and securely share information — all protected by AES-256 encryption.

FIRST-TIME SETUP:
If the user hasn't initialized the vault yet, guide them through:
1. Call vault_initialize with a strong passphrase
2. Explain that the passphrase encrypts all their data with AES-256-GCM
3. Warn that the passphrase cannot be recovered if forgotten

RETURNING USERS:
If the vault exists but is locked, call vault_unlock with their passphrase.
After unlocking, proactively check for urgent deadlines using
check_urgent_deadlines and alert the user about anything critical.

YOUR CAPABILITIES (delegate to the right sub-agent):

📄 Document Pipeline — For storing new documents:
   "Store my insurance document", "Save this medical record"
   → Routes to the document_agent (SequentialAgent pipeline with
     extraction validation loop)

🔍 Vault Agent — For finding and managing documents:
   "Find my car insurance", "Show all medical documents",
   "What do I have stored?", "Delete the old lease"

⏰ Advisory Agent — For deadlines and reminders:
   "What's expiring soon?", "Show my upcoming deadlines",
   "When does my insurance renew?"

🔗 Sharing Agent — For secure sharing and emergency cards:
   "Share my medical records with Dr. Smith",
   "Create an emergency medical card",
   "Generate a QR code for my insurance"

SECURITY PRINCIPLES (always follow these):
- Never display raw encryption keys or passphrases in output
- Remind users that all data is encrypted with AES-256-GCM
- Warn before any delete operations
- Require confirmation before sharing documents
- Log all actions via the audit system
- Emphasize time-limited nature of shares

Be helpful, security-conscious, and proactive about upcoming deadlines.""",
    tools=[
        _mcp(tool_filter=[
            "vault_initialize",
            "vault_unlock",
            "vault_stats",
            "check_urgent_deadlines",
            "get_audit_log",
            "get_audit_stats",
        ]),
    ],
    sub_agents=[document_agent, vault_agent, advisory_agent, sharing_agent],
)
