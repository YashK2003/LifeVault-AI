# LifeVault — Secure Personal Life Management Concierge

> Your life, organized. Your data, encrypted. Your agent, always watching out.

LifeVault is a multi-agent AI system that acts as your encrypted personal vault and intelligent life assistant. Upload any document — insurance policies, medical records, leases, warranties, receipts — and LifeVault extracts, encrypts, and stores the key information. It proactively reminds you about expirations, renewals, and deadlines, and lets you securely share scoped summaries via time-limited QR codes.

**Track:** Concierge Agents  
**Course:** Kaggle 5-Day AI Agents: Intensive Vibe Coding Course with Google

## Architecture

<!-- Architecture diagram will go here -->

### Multi-Agent System (Google ADK)

| Agent | Role |
|---|---|
| **Orchestrator** | Routes queries to the right sub-agent |
| **Document Agent** | Extracts structured data from uploads (OCR + LLM) |
| **Vault Agent** | Encrypted CRUD operations via custom MCP |
| **Advisory Agent** | Monitors deadlines, generates proactive alerts |
| **Sharing Agent** | Creates time-limited encrypted shares + QR codes |

### Custom MCP Server

The LifeVault MCP Server provides 12+ tools for encrypted document storage, semantic search, audit logging, and secure sharing.

### Security

- AES-256-GCM encryption at rest
- PBKDF2 key derivation from user passphrase
- Per-document encryption envelopes
- Role-based access control
- Time-limited sharing with auto-revocation
- Complete audit trail

## Course Concepts Demonstrated

| Concept | Implementation |
|---|---|
| Agent / Multi-agent (ADK) | 5 specialized agents with orchestration |
| MCP Server | Custom encrypted vault MCP with 12+ tools |
| Antigravity IDE | Built with TDD workflow |
| Security Features | AES-256, RBAC, audit logs, scoped sharing |
| Deployability | Docker + Google Cloud Run |
| Agent Skills / CLI | CLI interface for all operations |

## Setup

### Prerequisites
- Python 3.10+
- Google Gemini API key ([get one here](https://aistudio.google.com/apikey))

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/lifevault.git
cd lifevault
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your GOOGLE_API_KEY
```

### Run

```bash
# Start the MCP server
python -m mcp_server.server

# In another terminal, run the agent
adk run agents/
```

## Security & Performance Tradeoffs

Embeddings are encrypted at rest and decrypted in-memory for semantic search. At personal-vault scale (~hundreds of documents), this adds negligible latency (<10ms for 500 vectors). For larger deployments, approximate nearest neighbor indices (FAISS/ScaNN) with encrypted shards would be the scaling path.

## License

MIT
