# CLAUDE.md — Christophe.AI

Project-specific instructions for Claude Code working on this codebase.

---

## What this project is

**Christophe.AI** — a WhatsApp AI agent that acts as an autonomous intermediary between a tenant
(Thomas Martin) and a landlord (Marie Dubois) for a furnished studio at 42 rue de la Roquette,
75011 Paris. Christophe doesn't just answer questions — he acts: contacts the other party, runs
web searches, generates legal documents, coordinates schedules, and tracks every workflow with
self-maintained plans.

The agent runs in French. Code, comments, and the system prompt are in French. This file and
the README are in English for accessibility.

---

## Architecture overview

```
WhatsApp message (Thomas or Marie)
    ↓
Twilio webhook → FastAPI /webhook
    ↓
agent/main.py — routes by phone number, builds context
    ↓
agent/brain.py — agentic loop (Claude Sonnet 4.6 + tools, max 10 iterations)
    │
    ├── reads journal (auto-logged facts)
    ├── reads active dossier (current plan with milestones)
    ├── injects both into the system prompt
    ↓
Claude decides which tools to call → tools_exec.py executes them
    ↓
Response sent back via Twilio (or to a second party via contacter_partie)
```

---

## Key modules

| Module | Role |
|--------|------|
| `agent/main.py` | FastAPI server, webhook routing, identity detection by phone |
| `agent/brain.py` | Agentic loop — calls Claude repeatedly until a final answer, injects journal+dossier into every system prompt |
| `agent/tools_exec.py` | 12 tools and their JSON schemas: web search, contact party, document generation, plan management, etc. Also holds the auto-log hook |
| `agent/dossiers.py` | Active workflow plan with milestones. `creer_dossier`, `reviser_dossier`, `mettre_a_jour_milestone` |
| `agent/journal.py` | Auto-written log of completed actions. Written by the system (not Claude), injected at every turn. The single source of truth for what's been done |
| `agent/notes.py` | Free key/value memory for cross-conversation facts |
| `agent/memory.py` | Per-phone conversation history (last 20 messages) + DB initialization |
| `agent/repairs.py` | Repair request lifecycle |
| `agent/documents.py` | PDF generation (currently quittance de loyer) via fpdf2 |
| `agent/providers/twilio.py` | Twilio WhatsApp send/receive + media attachment via `PUBLIC_URL` |
| `agent/db.py` | Shared SQLAlchemy async engine + `Base` |
| `config/prompts.yaml` | System prompt — identity, responsibility matrix, plan protocol |
| `config/people.yaml` | Tenant and landlord names + phone numbers |
| `tests/interface_locale/server.py` | Local web UI (port 7777) — three-column WhatsApp-style simulator with real-time activity feed, dossier panel, journal panel. Bypasses Twilio entirely |

---

## The plan protocol — the core mechanism

This is the architectural keystone. Without it, the agent forgets, repeats, contradicts itself.

**At the start of every turn:**

1. The system auto-injects into the prompt:
   - The journal of completed actions (written by `tools_exec.py`, not by Claude)
   - The active dossier with its milestones
   - An explicit instruction to review the plan before acting

2. Claude's first responsibility is to review whether the active dossier still matches reality:
   - No active dossier → call `creer_dossier`
   - Active dossier covers the new message → just update milestones
   - Active dossier is incomplete or obsolete → call `reviser_dossier` to add/remove/reorder steps
   - New unrelated subject → `creer_dossier` again (the old one gets canceled)

3. Throughout the response, Claude must mark milestones FAIT immediately as they complete,
   not in a batch at the end.

**Why this works:**

- The journal is written by code, so Claude can't forget to update it
- The dossier is shown at every turn, so Claude can't claim ignorance
- The agent loop is bounded (10 iterations), so runaway loops are impossible
- A safeguard in `brain.py` auto-creates a generic dossier if Claude skips `creer_dossier`
  on the first iteration of a request with no active dossier

---

## Running the project

### Local UI (the best dev experience, no Twilio quota)

```bash
source .venv/bin/activate
python3 tests/interface_locale/server.py
# opens at http://localhost:7777
```

Three columns: Thomas | Christophe activity | Marie. Send messages as either party, watch the
agent's tool calls, dossier evolution, and journal in real time.

### Production WhatsApp (Twilio)

```bash
source .venv/bin/activate
uvicorn agent.main:app --port 8000
ngrok http 8000
# point Twilio sandbox webhook to https://<ngrok>/webhook
# set PUBLIC_URL=https://<ngrok> in .env so PDF attachments work
```

### Reset everything

Send the message `clean` from any chat → wipes conversation history, repairs, notes, dossiers,
and journal. Same effect as the "Nouvelle conversation" button in the local UI.

---

## Environment variables

```env
ANTHROPIC_API_KEY=sk-ant-...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+14155238886         # Twilio WhatsApp sandbox number
TAVILY_API_KEY=tvly-...                  # for recherche_web
PUBLIC_URL=https://<ngrok>.ngrok.io      # required for PDF attachments via Twilio
DATABASE_URL=sqlite+aiosqlite:///./agentkit.db
PORT=8000
ENVIRONMENT=development
```

---

## Conventions

- Code, comments, and the system prompt are in French — keep that language
- File and function names follow French naming (`creer_dossier`, `effacer_journal`, etc.)
- SQLAlchemy 2.0 typed-mapped style with `async_session`
- Every significant tool action gets auto-logged in the journal via `_auto_log` in `tools_exec.py`
- WebSocket events for the local UI: `message_in`, `outbound`, `reply`, `tool_call`,
  `journal_entry`, `dossier_update`, `document`, `thinking`, `clean`
- No emojis in code unless they're part of UI strings (journal labels, milestone icons)

---

## Common gotchas

- `contacter_partie` saves the outbound message to the recipient's conversation history.
  Without this, the recipient's next reply has no context for Claude
- The `_broadcast_hook` pattern in `tools_exec.py`, `journal.py`, `dossiers.py` is how the local
  UI gets real-time updates. In production (Twilio), these hooks stay `None` and nothing breaks
- `PUBLIC_URL` must be set for Twilio document attachments — the file is served from
  `documents/{filename}` and Twilio fetches it
- The garde-fou in `brain.py` only creates a generic dossier on iteration 0 if Claude omitted
  `creer_dossier` AND no dossier was already active. Don't expect it to compensate for
  systemic prompt issues
