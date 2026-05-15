# Christophe.AI — WhatsApp Intermediary Agent for Property Management

Christophe is an AI agent that acts as a full intermediary between **tenants and landlords** over WhatsApp. No app to download, no account to create — just a WhatsApp message.

---

## What Christophe does

A tenant messages about a broken boiler. Christophe collects the details, contacts the landlord for approval, proposes repair slots, and confirms the appointment to both parties. The humans only step in for key decisions.

```
Tenant: "My oven stopped working"
    → Christophe collects: what happened, checks done, availability
    → Christophe notifies Marie (landlord) with a clean summary
    → Marie replies "yes" on WhatsApp
    → Christophe sends 3 repair slots to Thomas
    → Thomas picks slot 2
    → Both parties receive confirmation + PDF document if needed
```

Beyond repairs, Christophe handles rent receipts, lease questions, payment delays, and any coordination between the two parties — all autonomously.

---

## Architecture

```
Tenant (WhatsApp)                    Landlord (WhatsApp)
       |                                      |
       └──────────────┬───────────────────────┘
                      |
                 Twilio Sandbox
                 (HMAC-SHA1 signature validation)
                      |
                 ngrok tunnel
                      |
              FastAPI /webhook
                      |
         ┌────────────┴─────────────┐
         │    Identity detection    │
         │  (tenant vs landlord     │
         │   by phone number)       │
         └────────────┬─────────────┘
                      |
              agent/brain.py
              Agentic loop (max 10 iterations)
              Claude Sonnet 4.6 + 12 tools
                      |
         ┌────────────┴──────────────┐
         │  Context injected on      │
         │  every turn:              │
         │  - Journal (done actions) │
         │  - Active dossiers (plan) │
         │  - Conversation history   │
         └────────────┬──────────────┘
                      |
              SQLite (async)
              messages, notes, dossiers,
              journal, reparations
```

### The plan protocol

Every request is tracked as a **dossier** with milestones. At each turn, Christophe reviews the active plan, marks completed steps, and revises the plan if reality has changed. Multiple dossiers can be active simultaneously (e.g. a repair workflow and a rent receipt running in parallel).

The **journal** is written by the system (not by Claude) and injected into every prompt — it's the single source of truth for what has already been done.

---

## Tools (12)

| Tool | Description |
|------|-------------|
| `contacter_partie` | Send a WhatsApp message to the other party |
| `recherche_web` | Web search via Tavily |
| `sauvegarder_note` | Persist a key fact for future conversations |
| `lire_notes` | Read saved notes |
| `creer_reparation` | Open a repair request |
| `obtenir_reparations` | List active repair requests |
| `confirmer_rdv` | Confirm a repair appointment |
| `generer_et_envoyer_document` | Generate and send a PDF (rent receipt, etc.) |
| `creer_dossier` | Open a new workflow plan with milestones |
| `mettre_a_jour_milestone` | Mark a plan step as done / in progress |
| `reviser_dossier` | Restructure the active plan |
| `lire_dossier_actif` | Read all active dossiers |

---

## Stack

| Component | Technology |
|-----------|-----------|
| AI | Claude Sonnet 4.6 (Anthropic) |
| Server | FastAPI + Uvicorn |
| WhatsApp | Twilio sandbox |
| Memory | SQLite via SQLAlchemy async (aiosqlite) |
| PDF generation | fpdf2 |
| Web search | Tavily |
| Tunnel (dev) | ngrok |
| Config | python-dotenv + YAML |

---

## Project structure

```
├── agent/
│   ├── main.py           FastAPI webhook + identity routing + clean command
│   ├── brain.py          Agentic loop — Claude + tools, journal + dossier injection
│   ├── tools_exec.py     12 tools with JSON schemas + auto-journal hook
│   ├── dossiers.py       Multi-workflow plan tracking (milestones)
│   ├── journal.py        Auto-written action log (source of truth)
│   ├── memory.py         Conversation history per phone number
│   ├── notes.py          Persistent key-value memory
│   ├── repairs.py        Repair request lifecycle
│   ├── documents.py      PDF generation (rent receipts) — legal fields from bail.yaml only
│   ├── db.py             Shared SQLAlchemy async engine
│   └── providers/
│       ├── base.py       Abstract WhatsApp provider
│       ├── twilio.py     Twilio adapter + HMAC-SHA1 signature validation
│       └── __init__.py   Provider factory
├── config/
│   ├── prompts.yaml      System prompt — identity, responsibility matrix, plan protocol
│   ├── bail.yaml         Authoritative lease data (rent, parties, address)
│   ├── business.yaml     Additional business context
│   └── people.yaml       Phone number → identity mapping (gitignored — see people.example.yaml)
├── documents/            Generated PDFs served via PUBLIC_URL
├── tests/
│   └── interface_locale/
│       └── server.py     Local 3-column web UI (port 7777) — no Twilio needed
├── .env                  API keys (never committed)
└── requirements.txt
```

---

## Setup

### Requirements

- Python 3.11+
- Anthropic API key (`sk-ant-...`)
- Twilio account (sandbox, free)
- Tavily API key (free tier available)
- ngrok account (free)

### Install

```bash
git clone https://github.com/cggailla/christophe.ai.git
cd christophe.ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

Copy and fill in `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
WHATSAPP_PROVIDER=twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+14155238886
TAVILY_API_KEY=tvly-...
PUBLIC_URL=https://<your-ngrok>.ngrok-free.app   # required for PDF attachments
PORT=8000
ENVIRONMENT=development
DATABASE_URL=sqlite+aiosqlite:///./agentkit.db
```

Copy and fill in `config/people.yaml` (see `config/people.example.yaml`):

```yaml
landlord:
  name: "Marie Dubois"
  phone: "+33..."

tenant:
  name: "Thomas Martin"
  phone: "+33..."
```

Both numbers must have joined the Twilio WhatsApp sandbox (`join <sandbox-word>` sent to the Twilio number).

### Run

```bash
# Start the API server
source .venv/bin/activate
uvicorn agent.main:app --port 8000

# Expose it publicly
./ngrok http 8000
```

Set the ngrok HTTPS URL + `/webhook` as the **"When a message comes in"** URL in the Twilio sandbox settings.

### Test locally (no WhatsApp, no Twilio quota)

```bash
source .venv/bin/activate
python3 tests/interface_locale/server.py
# Open http://localhost:7777
```

Three-column interface: Thomas | Christophe activity feed | Marie. Real-time tool calls, dossier evolution, journal, and generated documents.

---

## Special commands

Send `clean` from any known WhatsApp number to wipe all conversation memory and reset to a blank slate. Useful between demos.

---

## Security

- **Twilio signature validation** — every incoming webhook is verified with HMAC-SHA1 against the auth token. Invalid signatures are rejected silently.
- **Phone number allowlist** — messages from unknown numbers are dropped before reaching the agent.
- **asyncio.Lock per phone** — concurrent messages from the same number are serialized, preventing race conditions.
- **PDF forgery prevention** — legal fields (names, amounts, address) are sourced from `config/bail.yaml` only, never from LLM-supplied input.
- **Prompt injection guard** — user message content in the journal is wrapped in XML tags with an explicit warning to Claude to treat it as data, not instructions.

---

## Key design decisions

**Why a single agent context instead of separate tenant/landlord brains?**
The same Christophe instance handles both parties. Identity is injected into the system prompt at each turn (`speaker: "locataire"` or `"bailleur"`). This keeps the responsibility matrix in one place and avoids duplicating context.

**Why a journal + dossier system?**
LLMs forget. The journal (written by code, not Claude) is injected at every turn so Claude can't claim it hasn't done something. The dossier (plan with milestones) prevents the agent from skipping steps or repeating actions already completed.

**Why `bail.yaml` as the source of truth for documents?**
Rent receipts are legal documents. Allowing the LLM to supply the tenant's name or rent amount would be a forgery vector — any injected message could override them. `bail.yaml` is read-only at document generation time.

---

## License

MIT
