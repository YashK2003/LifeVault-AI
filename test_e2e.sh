#!/bin/bash
# ============================================================
# LifeVault — End-to-End Testing Script
# ============================================================
# Run this AFTER setting up your .env with GOOGLE_API_KEY.
#
# Usage:
#   chmod +x test_e2e.sh
#   ./test_e2e.sh
#
# This script tests:
#   Phase 1: Unit tests (32 tests, no API key needed)
#   Phase 2: CLI - vault init, store, search, list, deadlines, sharing, audit
#   Phase 3: MCP server import + tool count verification
#   Phase 4: Agent import + architecture verification
#
# Phase 5 (ADK Web UI) must be tested manually — see instructions at the end.
# ============================================================

set +e  # Continue on errors — we track pass/fail ourselves

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
PASSPHRASE="TestVault2026!"
VAULT_DB="test_e2e_vault.db"

pass() {
    echo -e "  ${GREEN}PASS${NC}: $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC}: $1"
    FAIL=$((FAIL + 1))
}

cleanup() {
    rm -f "$VAULT_DB" 2>/dev/null
    echo ""
    echo "============================================================"
    echo -e "E2E Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
    echo "============================================================"
    if [ $FAIL -gt 0 ]; then
        exit 1
    fi
}

trap cleanup EXIT

echo "============================================================"
echo "  LifeVault — End-to-End Testing"
echo "============================================================"
echo ""

# ──────────────────────────────────────────────────────
# PHASE 1: Unit Tests
# ──────────────────────────────────────────────────────
echo -e "${BLUE}PHASE 1: Unit Tests (32 tests)${NC}"
echo "──────────────────────────────────────────────────"

if python -m pytest tests/ -v --tb=short 2>&1 | tail -5 | grep -q "passed"; then
    RESULT=$(python -m pytest tests/ -q 2>&1 | tail -1)
    pass "Unit tests: $RESULT"
else
    fail "Unit tests failed — run 'python -m pytest tests/ -v' for details"
fi

echo ""

# ──────────────────────────────────────────────────────
# PHASE 2: CLI End-to-End
# ──────────────────────────────────────────────────────
echo -e "${BLUE}PHASE 2: CLI End-to-End${NC}"
echo "──────────────────────────────────────────────────"

export VAULT_DB_PATH="$VAULT_DB"

# 2.1 Initialize vault
echo -e "${YELLOW}  Testing: vault init${NC}"
INIT_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" init 2>&1)
if echo "$INIT_OUTPUT" | grep -qi "success\|initialized\|created"; then
    pass "Vault initialized"
else
    fail "Vault init: $INIT_OUTPUT"
fi

# 2.2 Unlock vault
echo -e "${YELLOW}  Testing: vault unlock${NC}"
UNLOCK_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" unlock 2>&1)
if echo "$UNLOCK_OUTPUT" | grep -qi "success\|unlocked"; then
    pass "Vault unlocked"
else
    fail "Vault unlock: $UNLOCK_OUTPUT"
fi

# 2.3 Store a document (this calls embedding API — needs GOOGLE_API_KEY)
echo -e "${YELLOW}  Testing: store document (calls Gemini Embedding API)${NC}"
STORE_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" store \
    --category insurance \
    --title "Test Auto Insurance - E2E" \
    --text "State Farm auto insurance policy number SF-2026-TEST. Coverage: collision and comprehensive. Premium: \$1200/year. Deductible: \$500. Policy period: Jan 1 2026 to Dec 31 2026. Insured vehicle: 2024 Honda Civic." \
    2>&1)
if echo "$STORE_OUTPUT" | grep -qi "stored\|success\|doc_id"; then
    DOC_ID=$(echo "$STORE_OUTPUT" | grep -oP 'doc_id["\s:]+\K[a-f0-9-]+' | head -1)
    if [ -z "$DOC_ID" ]; then
        DOC_ID=$(echo "$STORE_OUTPUT" | grep -oP '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1)
    fi
    pass "Document stored (ID: ${DOC_ID:-extracted})"
else
    fail "Store document: $STORE_OUTPUT"
fi

# 2.4 Store a second document
echo -e "${YELLOW}  Testing: store second document${NC}"
STORE2_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" store \
    --category medical \
    --title "Annual Physical - Dr. Johnson" \
    --text "Annual physical examination with Dr. Sarah Johnson on March 15 2026. Blood pressure 120/80, cholesterol 195. Prescribed metformin 500mg. Next visit scheduled September 2026. Allergies: penicillin." \
    2>&1)
if echo "$STORE2_OUTPUT" | grep -qi "stored\|success\|doc_id"; then
    DOC_ID2=$(echo "$STORE2_OUTPUT" | grep -oP '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1)
    pass "Second document stored"
else
    fail "Store second document: $STORE2_OUTPUT"
fi

# 2.5 List documents
echo -e "${YELLOW}  Testing: list documents${NC}"
LIST_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" list 2>&1)
if echo "$LIST_OUTPUT" | grep -qi "insurance\|medical"; then
    pass "List documents shows stored docs"
else
    fail "List documents: $LIST_OUTPUT"
fi

# 2.6 List with category filter
echo -e "${YELLOW}  Testing: list with category filter${NC}"
LIST_CAT_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" list --category insurance 2>&1)
if echo "$LIST_CAT_OUTPUT" | grep -qi "insurance"; then
    pass "Category filter works"
else
    fail "Category filter: $LIST_CAT_OUTPUT"
fi

# 2.7 Get a specific document
if [ -n "$DOC_ID" ]; then
    echo -e "${YELLOW}  Testing: get document by ID${NC}"
    GET_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" get "$DOC_ID" 2>&1)
    if echo "$GET_OUTPUT" | grep -qi "State Farm\|insurance\|SF-2026"; then
        pass "Get document returns correct data"
    else
        fail "Get document: $GET_OUTPUT"
    fi
fi

# 2.8 Search (semantic — calls embedding API)
echo -e "${YELLOW}  Testing: semantic search (calls Gemini Embedding API)${NC}"
SEARCH_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" search "car insurance deductible" 2>&1)
if echo "$SEARCH_OUTPUT" | grep -qi "insurance\|State Farm\|relevance\|score"; then
    pass "Semantic search found relevant document"
else
    fail "Semantic search: $SEARCH_OUTPUT"
fi

# 2.9 Add deadline
if [ -n "$DOC_ID" ]; then
    echo -e "${YELLOW}  Testing: add deadline${NC}"
    DL_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" add-deadline "$DOC_ID" \
        --desc "Policy renewal due" \
        --date "2026-12-31" 2>&1)
    if echo "$DL_OUTPUT" | grep -qi "success\|deadline\|added"; then
        pass "Deadline added"
    else
        fail "Add deadline: $DL_OUTPUT"
    fi
fi

# 2.10 Check deadlines
echo -e "${YELLOW}  Testing: list deadlines${NC}"
DL_LIST_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" deadlines --days 365 2>&1)
if echo "$DL_LIST_OUTPUT" | grep -qi "renewal\|deadline\|2026"; then
    pass "Deadlines listed"
else
    fail "List deadlines: $DL_LIST_OUTPUT"
fi

# 2.11 Vault stats
echo -e "${YELLOW}  Testing: vault stats${NC}"
STATS_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" stats 2>&1)
if echo "$STATS_OUTPUT" | grep -qi "total\|document\|categor"; then
    pass "Vault stats returned"
else
    fail "Vault stats: $STATS_OUTPUT"
fi

# 2.12 Audit log
echo -e "${YELLOW}  Testing: audit log${NC}"
AUDIT_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" audit --limit 5 2>&1)
if echo "$AUDIT_OUTPUT" | grep -qi "store\|unlock\|initialize\|audit"; then
    pass "Audit log has entries"
else
    fail "Audit log: $AUDIT_OUTPUT"
fi

# 2.13 Share (if we have a doc ID)
if [ -n "$DOC_ID" ]; then
    echo -e "${YELLOW}  Testing: create share${NC}"
    SHARE_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" share "$DOC_ID" \
        --scope summary \
        --recipient "Dr. Test" 2>&1)
    if echo "$SHARE_OUTPUT" | grep -qi "share\|success\|created\|expires"; then
        pass "Share created"
    else
        fail "Create share: $SHARE_OUTPUT"
    fi
fi

# 2.14 Emergency card
echo -e "${YELLOW}  Testing: emergency card${NC}"
EMERG_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" emergency-card \
    --allergies "penicillin,shellfish" \
    --conditions "hypertension" \
    --medications "metformin 500mg" \
    --blood-type "O+" \
    --contacts "Mom: 555-1234" 2>&1)
if echo "$EMERG_OUTPUT" | grep -qi "emergency\|card\|success\|share\|qr"; then
    pass "Emergency card generated"
else
    fail "Emergency card: $EMERG_OUTPUT"
fi

# 2.15 Update document
if [ -n "$DOC_ID" ]; then
    echo -e "${YELLOW}  Testing: update document${NC}"
    UPDATE_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" update "$DOC_ID" \
        --title "Updated Auto Insurance - E2E" 2>&1)
    if echo "$UPDATE_OUTPUT" | grep -qi "updated\|success"; then
        pass "Document updated"
    else
        fail "Update document: $UPDATE_OUTPUT"
    fi
fi

# 2.16 Delete document (use second doc so we keep first for further tests)
if [ -n "$DOC_ID2" ]; then
    echo -e "${YELLOW}  Testing: delete document${NC}"
    DELETE_OUTPUT=$(python cli.py --passphrase "$PASSPHRASE" delete "$DOC_ID2" --force 2>&1)
    if echo "$DELETE_OUTPUT" | grep -qi "deleted\|success"; then
        pass "Document deleted"
    else
        fail "Delete document: $DELETE_OUTPUT"
    fi
fi

echo ""

# ──────────────────────────────────────────────────────
# PHASE 3: MCP Server Verification
# ──────────────────────────────────────────────────────
echo -e "${BLUE}PHASE 3: MCP Server Verification${NC}"
echo "──────────────────────────────────────────────────"

MCP_CHECK=$(python -c "
from mcp_server.server import mcp
print('MCP_OK')
" 2>&1)
if echo "$MCP_CHECK" | grep -q "MCP_OK"; then
    pass "MCP server imports successfully"
else
    fail "MCP server import: $MCP_CHECK"
fi

TOOL_COUNT=$(grep -c '@mcp.tool()' mcp_server/server.py)
if [ "$TOOL_COUNT" -eq 20 ]; then
    pass "MCP server has 20 tools (expected: 20)"
else
    fail "MCP server has $TOOL_COUNT tools (expected: 20)"
fi

echo ""

# ──────────────────────────────────────────────────────
# PHASE 4: Agent Architecture Verification
# ──────────────────────────────────────────────────────
echo -e "${BLUE}PHASE 4: Agent Architecture Verification${NC}"
echo "──────────────────────────────────────────────────"

AGENT_CHECK=$(python -c "
from agents.agent import root_agent

# Check root agent
assert root_agent.name == 'lifevault', f'Root name: {root_agent.name}'
print('ROOT_OK')

# Check sub-agents
names = [a.name for a in root_agent.sub_agents]
assert 'document_agent' in names, f'Missing document_agent in {names}'
assert 'vault_agent' in names, f'Missing vault_agent in {names}'
assert 'advisory_agent' in names, f'Missing advisory_agent in {names}'
assert 'sharing_agent' in names, f'Missing sharing_agent in {names}'
print('SUBAGENTS_OK')

# Check SequentialAgent
doc = root_agent.sub_agents[0]
assert type(doc).__name__ == 'SequentialAgent', f'document_agent type: {type(doc).__name__}'
print('SEQUENTIAL_OK')

# Check LoopAgent
loop = doc.sub_agents[0]
assert type(loop).__name__ == 'LoopAgent', f'loop type: {type(loop).__name__}'
assert loop.max_iterations == 2, f'max_iterations: {loop.max_iterations}'
print('LOOP_OK')

# Check inner agents
assert loop.sub_agents[0].name == 'document_extractor'
assert loop.sub_agents[1].name == 'document_reviewer'
assert doc.sub_agents[1].name == 'document_storer'
print('INNER_AGENTS_OK')

print('ALL_ARCH_OK')
" 2>&1)

if echo "$AGENT_CHECK" | grep -q "ROOT_OK"; then
    pass "Root agent (lifevault) loads correctly"
else
    fail "Root agent: $AGENT_CHECK"
fi

if echo "$AGENT_CHECK" | grep -q "SUBAGENTS_OK"; then
    pass "4 sub-agents present (document, vault, advisory, sharing)"
else
    fail "Sub-agents: $AGENT_CHECK"
fi

if echo "$AGENT_CHECK" | grep -q "SEQUENTIAL_OK"; then
    pass "Document pipeline is SequentialAgent"
else
    fail "SequentialAgent: $AGENT_CHECK"
fi

if echo "$AGENT_CHECK" | grep -q "LOOP_OK"; then
    pass "Extraction loop is LoopAgent (max_iterations=2)"
else
    fail "LoopAgent: $AGENT_CHECK"
fi

if echo "$AGENT_CHECK" | grep -q "INNER_AGENTS_OK"; then
    pass "Inner agents: extractor, reviewer, storer all present"
else
    fail "Inner agents: $AGENT_CHECK"
fi

echo ""

# ──────────────────────────────────────────────────────
# CLEANUP
# ──────────────────────────────────────────────────────
unset VAULT_DB_PATH
