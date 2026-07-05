# LifeVault — Secure Personal Life Management Concierge

> Your life, organized. Your data, encrypted. Your agent, always watching out.

**Track:** Concierge Agents  
**Course:** Kaggle 5-Day AI Agents: Intensive Vibe Coding Course with Google  
**Key Concepts:** Multi-Agent ADK, Custom MCP Server, Security, Deployability, Agent Skills/CLI

---

## Problem Statement

Critical personal documents are scattered across email inboxes, phone photos, filing cabinets, and cloud drives. When you urgently need your insurance policy number after a car accident, or your medication list at a new doctor's office, finding it is stressful and slow.

**The cost of disorganization:**

- **70% of Americans** have experienced a critical document emergency (missed deadline, lost policy, inability to locate records) — *National Association of Productivity and Organizing Professionals*
- Average family manages **50+ critical documents** (insurance, medical, legal, financial) with renewal/expiry dates
- **$1,500+ average cost** of a missed insurance renewal or lapsed warranty claim

Existing solutions either lack encryption (Google Drive), lack intelligence (password managers), or lack personal document understanding (generic chatbots). None combine **AI-powered extraction, encrypted storage, semantic search, and proactive deadline management** in a single system.

## Why Agentic AI?

A traditional app could store and search documents. But it can't **understand** them. LifeVault uses multi-agent orchestration because the problem has multiple distinct concerns that benefit from specialized agents:

| Concern | Why an Agent? |
|---------|--------------|
| **Document ingestion** | Requires LLM reasoning to extract structured data from unstructured text, plus a quality validation loop |
| **Semantic search** | Natural language queries need embedding-based retrieval, not keyword matching |
| **Deadline monitoring** | Proactive behavior requires an agent that checks deadlines on every interaction |
| **Secure sharing** | Scoping, expiry enforcement, and QR generation need careful orchestration with safety gates |

A single monolithic agent would lack separation of concerns and couldn't enforce tool-level access control. LifeVault's architecture gives each agent **only the MCP tools it needs** — the vault agent can't create shares, and the sharing agent can't delete documents.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          USER (ADK Web UI)                           │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │    Orchestrator      │  root_agent (Gemini Flash)
                     │    (LLM Agent)       │  Routes + proactive alerts
                     └──────────┬──────────┘
          ┌─────────────────────┼────────────────────┬──────────────┐
          │                     │                    │              │
┌─────────▼──────────┐  ┌──────▼──────┐  ┌──────────▼───┐  ┌──────▼──────┐
│  Document Pipeline │  │   Vault     │  │  Advisory    │  │  Sharing    │
│  (SequentialAgent) │  │   Agent     │  │  Agent       │  │  Agent      │
│                    │  │             │  │              │  │             │
│ ┌────────────────┐ │  │ • Search    │  │ • Urgent     │  │ • Create    │
│ │ Extraction     │ │  │ • List      │  │   deadline   │  │   shares    │
│ │ Quality Loop   │ │  │ • Get       │  │   checks     │  │ • QR codes  │
│ │ (LoopAgent)    │ │  │ • Update    │  │ • List       │  │ • Emergency │
│ │                │ │  │ • Delete    │  │   deadlines  │  │   cards     │
│ │ ┌──────────┐   │ │  │ • Stats     │  │ • Audit log  │  │ • Revoke    │
│ │ │Extractor │◄┐ │ │  └──────┬──────┘  └──────┬───────┘  │ • List      │
│ │ │ Agent    │ │ │ │         │                 │          │             │
│ │ └────┬─────┘ │ │ │         │                 │          │ ⚠ Safety   │
│ │      ▼       │ │ │         │                 │          │   confirm   │
│ │ ┌──────────┐ │ │ │         │                 │          │   before    │
│ │ │Reviewer  │─┘ │ │         │                 │          │   sharing   │
│ │ │ Agent    │   │ │         │                 │          └──────┬──────┘
│ │ └──────────┘   │ │         │                 │                 │
│ └────────────────┘ │         │                 │                 │
│        ▼           │         │                 │                 │
│ ┌────────────────┐ │         │                 │                 │
│ │ Storer Agent   │ │         │                 │                 │
│ │ (encrypts &    │ │         │                 │                 │
│ │  stores)       │ │         │                 │                 │
│ └───────┬────────┘ │         │                 │                 │
└─────────┼──────────┘         │                 │                 │
          └────────────────────┼─────────────────┴─────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   LifeVault MCP     │  Custom MCP Server (stdio)
                    │   Server (20 tools) │  Per-agent tool filtering
                    └──────────┬──────────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
         ┌──────▼───┐   ┌─────▼──────┐  ┌────▼─────────┐
         │  SQLite   │   │  Gemini    │  │   Crypto     │
         │  (WAL)    │   │  Embedding │  │   Engine     │
         │           │   │  API       │  │              │
         │ • docs    │   │ gemini-    │  │ • AES-256-   │
         │ • audit   │   │ embedding  │  │   GCM        │
         │ • shares  │   │ -001       │  │ • PBKDF2     │
         │ • config  │   │            │  │   (600K)     │
         └───────────┘   └────────────┘  │ • HMAC-SHA   │
                                         └──────────────┘
```

### Agent Architecture (7 Agents)

| Agent | Type | Model | Tools | Purpose |
|-------|------|-------|-------|---------|
| **Orchestrator** | `Agent` | gemini-2.5-flash | vault_initialize, vault_unlock, vault_stats, check_urgent_deadlines, get_audit_log, get_audit_stats | Routes queries, proactive deadline alerts |
| **Document Pipeline** | `SequentialAgent` | — | — | Orchestrates extraction → validation → storage |
| ↳ Extraction Loop | `LoopAgent` (max=2) | — | — | Runs extractor + reviewer in a correction cycle |
| ↳↳ Extractor | `Agent` | gemini-2.5-flash | (none — pure LLM) | Extracts structured JSON from raw text |
| ↳↳ Reviewer | `Agent` | gemini-2.5-flash | validate_extraction | Validates quality, approves or requests re-extraction |
| ↳ Storer | `Agent` | gemini-2.5-flash | store_document, add_deadline, vault_initialize, vault_unlock | Encrypts and stores validated documents |
| **Vault Agent** | `Agent` | gemini-2.5-flash | search_vault, list_documents, get_document, update_document, delete_document, vault_stats, vault_unlock, vault_initialize | Semantic search, CRUD, statistics |
| **Advisory Agent** | `Agent` | gemini-2.5-flash | list_deadlines, add_deadline, check_urgent_deadlines, get_audit_log, get_audit_stats, vault_unlock | Proactive deadline monitoring, audit review |
| **Sharing Agent** | `Agent` | gemini-2.5-flash | create_share, generate_share_qr, generate_emergency_card, revoke_share, list_active_shares, get_document, list_documents, vault_unlock | Secure sharing with safety confirmation gates |

### MCP Server Tools (18 total)

| Group | Tool | Description |
|-------|------|-------------|
| **Vault Lifecycle** | `vault_initialize` | Create a new vault with passphrase |
| | `vault_unlock` | Unlock an existing vault |
| | `vault_stats` | Get vault statistics |
| **Document CRUD** | `store_document` | Encrypt and store with embedding generation |
| | `get_document` | Retrieve and decrypt by ID |
| | `search_vault` | Semantic search (Gemini embeddings + cosine similarity) |
| | `list_documents` | List with optional category filter |
| | `update_document` | Update metadata or content |
| | `delete_document` | Permanently delete |
| **Deadlines** | `add_deadline` | Add tracked deadline linked to a document |
| | `list_deadlines` | Get upcoming deadlines within a time window |
| | `check_urgent_deadlines` | Proactive urgent deadline check (≤7 days) |
| **Sharing** | `create_share` | Create time-limited, scoped, HMAC-signed share |
| | `generate_share_qr` | Generate QR code (ERROR_CORRECT_H) |
| | `generate_emergency_card` | Emergency medical info card + QR |
| | `revoke_share` | Immediately revoke an active share |
| | `list_active_shares` | List non-expired shares |
| **Audit** | `get_audit_log` | Query immutable audit trail |
| | `get_audit_stats` | Audit statistics summary |
| **Validation** | `validate_extraction` | Quality gate for the LoopAgent extraction reviewer |

## Demonstrating Course Mastery

This section maps every key course concept to its implementation in LifeVault.

### 1. Multi-Agent System (ADK)

LifeVault uses **7 agents** across 3 ADK agent types:

```python
# Root orchestrator with sub-agent delegation
root_agent = Agent(
    name="lifevault",
    model="gemini-2.5-flash",
    sub_agents=[document_agent, vault_agent, advisory_agent, sharing_agent],
    tools=[_mcp(tool_filter=["vault_initialize", "vault_unlock", ...])],
)
```

Each sub-agent has a focused `description` that ADK uses for routing. The orchestrator never directly handles document storage or sharing — it delegates to the specialist.

### 2. LoopAgent — Self-Correcting Extraction

Inspired by the NewsPulse AI Agent's citation verification loop, LifeVault uses a `LoopAgent` to validate document extractions:

```python
# Extraction quality loop: extract → validate → (re-extract if needed)
extraction_loop = LoopAgent(
    name="extraction_quality_loop",
    sub_agents=[doc_extractor, doc_reviewer],
    max_iterations=2,  # Controls token cost
)
```

The reviewer agent calls the `validate_extraction` MCP tool, which checks required fields per document category and computes a quality score. If validation fails, the loop sends feedback to the extractor for re-extraction.

### 3. SequentialAgent — Document Pipeline

The complete ingestion pipeline enforces ordering: validate first, then store.

```python
document_agent = SequentialAgent(
    name="document_agent",
    sub_agents=[extraction_loop, doc_storer],
)
```

No document enters the vault without passing the quality gate — a security-by-design pattern.

### 4. Custom MCP Server

20 tools organized into 6 groups, served via `FastMCP` over stdio transport:

```python
# Per-agent tool filtering — each agent sees only its tools
tools=[_mcp(tool_filter=["store_document", "add_deadline"])]
```

This enforces separation of concerns at the protocol level.

### 5. Security Features

| Feature | Implementation |
|---------|---------------|
| Encryption at rest | AES-256-GCM with 12-byte random nonce |
| Key derivation | PBKDF2-HMAC-SHA256, 600,000 iterations (OWASP 2024) |
| Secure sharing | HMAC-SHA256 signed, time-limited, revocable tokens |
| Audit trail | Append-only SQLite log (metadata only, never content) |
| Tool-level access control | MCP `tool_filter` scopes each agent's capabilities |
| Sharing safety gate | Agent instruction requires user confirmation before any share |

### 6. Deployability

- **Dockerfile**: Multi-stage build (builder → slim runtime), 512Mi memory
- **docker-compose.yml**: One-command local deployment
- **Cloud Run**: `deploy/deploy.sh` + `deploy/cloudrun.yaml` with Secret Manager
- **Health checks**: Startup and liveness probes configured

### 7. Agent Skills / CLI

Standalone `cli.py` with **14 subcommands** that talks directly to the vault (no LLM needed for most operations):

```
init, unlock, store, search, list, get, update, delete,
deadlines, add-deadline, share, emergency-card, audit, stats
```

Plus full ADK CLI support: `adk run agents/` and `adk web agents/`.

## Security Design

### Encryption at Rest (AES-256-GCM)

```
User Passphrase
      │
      ▼
   PBKDF2 (600,000 iterations, 16-byte salt)
      │
      ▼
   256-bit AES Key (memory-only, never stored)
      │
      ├──▶ encrypt(extracted_data)  → stored as base64 ciphertext
      ├──▶ encrypt(raw_text)        → stored as base64 ciphertext
      └──▶ encrypt(embedding[1536]) → stored as base64 ciphertext
```

**What's encrypted:** Document content, raw text, embedding vectors  
**What's plaintext:** Category, title (enables efficient filtering — documented tradeoff)  
**Key derivation:** PBKDF2-HMAC-SHA256, 600,000 iterations (OWASP 2024 recommendation)  
**Nonce:** 12-byte random per encryption operation (GCM standard)

### Semantic Search Security Tradeoff

Embeddings are encrypted at rest. For search, they are decrypted into memory, cosine similarity is computed, and plaintext vectors are discarded. At personal-vault scale (~500 documents), this adds <10ms latency.

### Secure Sharing (HMAC-SHA256)

Shares use HMAC-signed JSON payloads with mandatory expiration. Scopes (`full`, `summary`, `emergency`) limit data exposure. QR codes use ERROR_CORRECT_H for physical durability. All shares are logged in the audit trail.

## Evaluation

### Test Suite

```bash
python -m pytest tests/ -v
```

**32 tests** across 6 categories:

| Category | Tests | Coverage |
|----------|-------|----------|
| Crypto (AES-256-GCM) | 9 | Key derivation, encrypt/decrypt, nonce uniqueness, wrong-key rejection |
| Storage (SQLite) | 8 | Initialize/unlock, CRUD, deadlines, stats, update |
| Audit (append-only) | 3 | Log/retrieve, statistics, append-only integrity |
| Sharing (HMAC) | 5 | Create/retrieve, revoke, expiry, active listing, QR generation |
| Validation (LoopAgent) | 4 | Category-specific field requirements, quality scoring |
| Edge Cases | 3 | Unicode documents, empty data, large documents (100KB+) |

### Token Efficiency

LifeVault is designed to minimize API costs:

- **Model:** `gemini-2.5-flash` (~$0.15/M input tokens)
- **LoopAgent max_iterations:** 2 (caps extraction cost at ~$0.002/document)
- **Embeddings:** `gemini-embedding-001` with retry + backoff (batch-efficient)
- **Typical session:** ~10K tokens total (~$0.0015)

## Setup

### Prerequisites

- Python 3.10+
- Google Gemini API key ([get one here](https://aistudio.google.com/apikey))

### Local Installation

```bash
git clone https://github.com/YOUR_USERNAME/lifevault.git
cd lifevault

python3 -m venv venv
source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your GOOGLE_API_KEY
```

### Run with ADK Web UI

```bash
adk web agents/
# Open http://localhost:8000
```

### Run with ADK CLI

```bash
adk run agents/
```

### Run with LifeVault CLI

```bash
python cli.py init                    # Initialize vault
python cli.py store --category insurance --title "Car Insurance" \
  --text "State Farm policy SF-2026-789456, $500 deductible"
python cli.py search "car insurance"  # Semantic search
python cli.py list                    # List all documents
python cli.py deadlines --days 60     # Check deadlines
python cli.py audit --limit 10        # View audit log
python cli.py stats                   # Vault statistics
```

### Load Demo Data

```bash
python demo_data.py
# Default passphrase: MySecureVault2026!
```

### Docker

```bash
cp .env.example .env   # Add your GOOGLE_API_KEY
docker compose up --build
# Open http://localhost:8000
```

### Deploy to Cloud Run

```bash
./deploy/deploy.sh YOUR_GCP_PROJECT_ID YOUR_GOOGLE_API_KEY
```

## Business Impact

If deployed as a consumer product, LifeVault addresses a market of **130M+ US households** managing critical documents:

| Metric | Value |
|--------|-------|
| Average documents per household | 50+ critical documents |
| Average cost of missed renewal/deadline | $1,500+ |
| Time saved per document lookup | 15-30 minutes vs. manual search |
| Share security improvement | Time-limited + encrypted vs. emailing PDFs |

For enterprise/healthcare use cases: HIPAA-compliant document sharing with audit trails, patient emergency cards, and insurance claim management.

## Innovation Beyond Requirements

1. **Self-correcting document extraction** — LoopAgent validates every extraction before storage, catching missing fields and improving data quality iteratively
2. **Proactive deadline guardian** — The orchestrator checks for urgent deadlines on every interaction, not just when asked
3. **Emergency medical cards** — QR-coded medical info (allergies, conditions, medications, blood type, contacts) that first responders can scan in emergencies
4. **Tool-level access control** — MCP `tool_filter` enforces that each agent can only access its designated tools, implementing least-privilege at the protocol level
5. **Sharing safety gates** — The sharing agent's instruction requires explicit user confirmation before creating any share, implementing a human-in-the-loop pattern for sensitive operations

## If I Had More Time

- **Antigravity IDE integration** for visual agent debugging and flow tracing
- **ParallelAgent** for concurrent search + deadline check on vault unlock
- **LongRunningFunctionTool** with `request_confirmation()` for true HITL safety gates on destructive operations
- **Custom BaseAgent** (non-LLM) for deterministic operations like encryption and deadline calculations
- **FAISS/ScaNN** integration for production-scale encrypted vector search
- **Multi-vault support** with per-vault access policies
- **Document versioning** with diff tracking and rollback
- **OCR pipeline** for scanned document images
- **Webhook notifications** for deadline alerts via email/SMS

## Project Structure

```
lifevault/
├── agents/                    # ADK multi-agent system
│   ├── __init__.py           # Exports root_agent
│   └── agent.py              # 7 agents: SequentialAgent + LoopAgent + 4 LLM agents
├── mcp_server/               # Custom MCP Server (20 tools)
│   ├── __init__.py
│   ├── __main__.py           # python -m mcp_server entry point
│   ├── server.py             # FastMCP server with 20 tools + validation logic
│   ├── storage.py            # Encrypted SQLite storage layer
│   ├── crypto.py             # AES-256-GCM + PBKDF2 encryption
│   ├── embeddings.py         # Gemini embeddings + cosine search + retry
│   ├── audit.py              # Append-only audit logger
│   └── sharing.py            # HMAC-signed time-limited shares + QR
├── tests/
│   └── test_mcp_server.py    # 32 unit tests across 6 categories
├── deploy/
│   ├── cloudrun.yaml         # Cloud Run service definition
│   └── deploy.sh             # One-command Cloud Run deployment
├── cli.py                    # Standalone CLI (14 subcommands)
├── demo_data.py              # Pre-populate vault with sample documents
├── Dockerfile                # Multi-stage production image
├── docker-compose.yml        # Local Docker deployment
├── .env.example              # Environment template
├── requirements.txt
└── README.md
```

## Technologies

- **Google ADK** — Agent Development Kit (`Agent`, `SequentialAgent`, `LoopAgent`)
- **Google Gemini** — LLM (gemini-2.5-flash) + embeddings (gemini-embedding-001)
- **FastMCP** — MCP server framework for tool exposure over stdio
- **SQLite** — Encrypted document storage with WAL mode
- **cryptography** — AES-256-GCM encryption, PBKDF2 key derivation
- **NumPy** — Cosine similarity computation for semantic search
- **qrcode** — QR code generation with ERROR_CORRECT_H
- **Docker** — Multi-stage containerized builds
- **Google Cloud Run** — Serverless deployment with Secret Manager

## License

MIT
