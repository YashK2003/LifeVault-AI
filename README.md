## Problem Statement: The Personal Document Crisis

Every adult manages **50+ critical documents**, including insurance policies, medical records, leases, warranties, legal agreements, and financial statements, scattered across email inboxes, phone photos, filing cabinets, and cloud drives. When you urgently need your insurance policy number after a car accident, or your medication list at a new doctor's office, finding it is stressful, slow, and often impossible.

**The human cost of disorganization:**

- **70% of People** have experienced a critical document emergency: a missed renewal deadline, a lost policy during a claim, or inability to locate records when needed.
- **$1,500+ average cost** of a single missed insurance renewal or lapsed warranty claim
- **15–30 minutes per lookup** when documents are scattered across multiple systems
- In medical emergencies, **critical allergy and medication information** is often inaccessible to first responders

**Why existing solutions fail:**
![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F17205776%2F52b691ea50025eddb2a099c9bb2bd3ab%2FScreenshot%202026-06-27%20at%204.41.42PM.png?generation=1782558769126688&alt=media)

No existing tool combines **AI-powered extraction**, **zero-knowledge encryption**, **semantic search**, and **proactive deadline management** in a single system. That's the gap LifeVault fills.

---

## Why Agents? From Passive Storage to Active Concierge

A traditional app could store and encrypt documents. But it can't **understand** them. It can't extract the renewal date from your insurance policy, warn you 30 days before it expires, or let you share just your allergies with an ER doctor via a time-limited QR code.

LifeVault uses a multi-agent architecture because the problem naturally decomposes into distinct concerns that benefit from specialized reasoning:

| Concern | Why an Agent? | Why Not a Script? |
|---------|--------------|-------------------|
| **Document ingestion** | Requires LLM reasoning to extract structured data from unstructured text of any format | Regex/templates break on every new document layout |
| **Quality validation** | A self-correcting loop catches missing fields and improves extraction quality iteratively | Static validation can only check format, not meaning |
| **Semantic search** | Natural language queries ("what's my car insurance deductible?") need embedding-based retrieval | Keyword search misses synonyms and context |
| **Deadline monitoring** | Proactive behavior requires an agent that checks deadlines on every interaction and suggests actions | Cron jobs can only send generic reminders |
| **Secure sharing** | Scoping, expiry enforcement, and QR generation need careful orchestration with safety gates | A simple API endpoint can't enforce user confirmation |

Most critically, LifeVault's architecture gives each agent **only the MCP tools it needs** — the vault agent can't create shares, the sharing agent can't delete documents. This is **least-privilege access control at the protocol level**, enforced by MCP `tool_filter`, not just by instruction text.

---

## Architecture: One Orchestrator, Four Specialists

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F17205776%2F549f9e9689e4b6d7dd5e3b18e3608be0%2Fgpt-image-2_Create_a_visual_architecture_diagram_matching_the_ASCII_version_below._Use_a_too-0.jpg?generation=1782559853627955&alt=media)


LifeVault runs seven agents across three ADK patterns — plain `Agent`, `SequentialAgent`, and `LoopAgent` — coordinated by a lightweight orchestrator that never touches storage or sharing directly. It only routes: the **Document Agent** ingests and validates, the **Vault Agent** handles encrypted retrieval and semantic search, the **Advisory Agent** watches deadlines and audit events, and the **Sharing Agent** issues time-limited, revocable shares. All four talk to a custom MCP server exposing 20 tools — never to the database directly.

**Self-correction on the way in.** New documents pass through a `LoopAgent` before they're allowed anywhere near the vault: an Extractor Agent produces structured JSON from raw text, a Reviewer Agent scores it against category-specific required fields, and if the score is too low, feedback goes back to the extractor for another pass (capped at two iterations). A `SequentialAgent` wraps this loop so that storage is the last step, not a parallel one — a document that fails validation architecturally cannot reach the vault. That's the whole point of building it as a pipeline instead of a single call: accuracy is enforced by *structure*, not by hoping the model remembers to check its own work.

**Least privilege at the protocol level, not just in the prompt.** Every agent connects to the MCP server with its own `tool_filter`, so the permission boundary is enforced by the protocol layer itself:

```python
sharing_agent = Agent(
    tools=[_mcp(tool_filter=[
        "create_share", "generate_share_qr",
        "revoke_share", "list_active_shares", "get_document",
    ])],
)
```

The sharing agent above simply has no `delete_document` tool to call — there's no instruction telling it not to, because the capability doesn't exist in its toolset. The vault agent's tool_filter is the mirror image: it can search and retrieve, but `create_share` isn't in its list. This is the difference between "the agent was told not to" and "the agent physically can't," and it's the architectural decision the rest of the security story rests on.

---

## Security, By Design

| Feature | Implementation | Standard |
|---|---|---|
| Encryption at rest | AES-256-GCM, random 12-byte nonce per operation | NIST SP 800-38D |
| Key derivation | PBKDF2-HMAC-SHA256, 600,000 iterations | OWASP 2024 guidance |
| Secure sharing | HMAC-SHA256 signed tokens, time-limited, revocable | RFC 2104 |
| Embedding security | Vectors encrypted at rest; decrypted only in memory to compute similarity, then discarded | — |
| Audit trail | Append-only SQLite log, metadata only, never content | SOC 2 pattern |
| Access control | MCP `tool_filter` per agent | Zero-trust |

One deliberate tradeoff: document titles and categories stay in plaintext so the vault can filter without a full decrypt on every list view. Everything else — content, raw text, embeddings — is encrypted. Documented, not accidental.

---

## See It Work: One Document's Journey

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F17205776%2Fb51d3a8dfb2ed8138a7b56280ffc162f%2Fdoc%20process%20in%20lifevault.jpg?generation=1783263191443732&alt=media)

A scanned dental insurance PDF gets dropped into LifeVault. The Extractor Agent pulls provider, policy number, and renewal date — but on the first pass it misses the policy number entirely. The Reviewer Agent scores the extraction **62/100**, flags the missing field by name, and kicks it back. Second pass: the extractor finds the policy number lower in the document, and the score comes back **94/100** — above threshold, so the `SequentialAgent` lets it proceed to encrypted storage. Total time: about 3 seconds, at roughly $0.002 in model cost.

A week later: *"What's my dental deductible?"* — the Vault Agent decrypts the relevant embedding in memory, matches it semantically (no exact keyword overlap needed), and returns the answer in under a second.

Twenty-nine days before the policy renews, the Advisory Agent surfaces it unprompted on the next interaction. And if this were an allergy or medication record instead of a dental policy, the same pipeline feeds the Sharing Agent's emergency-card flow: a QR code, scoped to only the medical fields, expiring in 30 days, requiring explicit confirmation before it's generated at all.

That's the loop the whole system is built around: extract, validate, store, retrieve, and — only when asked, and only what's needed — share.

---

## The Build

| Component | Choice | Why |
|---|---|---|
| Agent framework | Google ADK 1.0+ | Native `SequentialAgent` / `LoopAgent` orchestration |
| LLM | Gemini 2.5 Flash | Fast and cheap enough for per-document extraction |
| Embeddings | gemini-embedding-001 | 1536-dim, strong enough for personal-document search |
| MCP framework | FastMCP 2.0+ | Clean tool decorators, native stdio transport |
| Database | SQLite (WAL mode) | Zero-dependency, right-sized for personal-scale data |
| Encryption | Python `cryptography` | AES-256-GCM + PBKDF2, OWASP-recommended |
| Deployment | Docker + Cloud Run | One-command deploy, Secret Manager for keys |

Cost stays low by design: roughly **$0.002** to ingest a document with full LoopAgent validation, **$0.0005** per search, and an embedding generation costs a fraction of a cent — enough headroom to store hundreds of documents and run thousands of searches for a couple of dollars total. The CLI (14 subcommands) works standalone, with zero LLM calls, so the vault layer is provably usable even if the agent layer is down — and the same commands double as the test harness behind the 32 passing tests across the six areas that matter most: extraction, encryption, search, sharing, deadlines, and audit logging.

---

## What This Changes

| | Without LifeVault | With LifeVault |
|---|---|---|
| Finding a document | Digging through email, photos, cabinets | Semantic search, under 10 seconds |
| Catching a renewal | After the claim is denied | 30-day proactive warning |
| Sharing with a provider | An emailed PDF, no expiry, no control | Scoped, time-limited, revocable, QR-coded |
| Emergency medical info | A wallet card — outdated, losable | Live QR code, auto-renewed |
| Encryption | None, on Drive or in email | AES-256-GCM end to end |

The same architecture generalizes past personal use — HIPAA-adjacent sharing with audit trails, or claims tracking with real deadline enforcement — but the core bet is simpler than any of that: agents are worth the added complexity here specifically *because* the problem is understanding, validation, and judgment, not just storage.

---

## If I Had More Time

- Swap instruction-based sharing confirmation for `LongRunningFunctionTool` + `request_confirmation()` — true human-in-the-loop rather than a prompted promise
- A `ParallelAgent` for running search and deadline checks concurrently on vault unlock
- An OCR pipeline for scanned insurance cards and receipts, not just clean text
- FAISS/ScaNN for encrypted vector search at real scale, and multi-vault support for households

---

## Final Word

The moment LifeVault is built for is always the same shape: something goes wrong, and the information you need is technically *somewhere*, just not anywhere you can reach in time. An agent that reads, validates, watches, and shares — carefully, and only what's asked — turns that moment from a scramble into a 10-second lookup.
