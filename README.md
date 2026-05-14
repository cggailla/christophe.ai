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
    → Both parties receive confirmation
```

Beyond repairs, Christophe answers any question about the lease: rent amount, due date, charge breakdown, deposit, past incidents — all from memory.

---

## Architecture

```
Tenant (WhatsApp)                    Landlord (WhatsApp)
       |                                      |
       └──────────────┬───────────────────────┘
                      |
                 Twilio Sandbox
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
          ┌───────────┴───────────┐
          |                       |
    Tenant brain            Landlord brain
    (Claude API             (Claude API
     + tool use)             + tool use)
          |                       |
    contacter_bailleur()    approuver_intervention()
                            refuser_intervention()
                      |
               SQLite (memory
               + repair state)
```

### Message flow

1. Tenant writes → webhook identifies them by phone number → tenant brain
2. Claude collects info (one question at a time), then calls `contacter_bailleur(summary, availability)`
3. Christophe proactively sends a WhatsApp to the landlord with the summary
4. Landlord replies → webhook identifies them → landlord brain
5. Claude interprets response, calls `approuver_intervention()` or `refuser_intervention()`
6. Christophe notifies the tenant with the outcome (repair slots or rejection reason)

### Repair states

```
WAITING_LANDLORD → CONFIRMED
                → REJECTED
```

---

## Stack

| Component | Technology |
|-----------|-----------|
| AI | Claude claude-sonnet-4-6 (Anthropic) |
| Server | FastAPI + Uvicorn |
| WhatsApp | Twilio sandbox |
| Memory | SQLite via SQLAlchemy async |
| Tunnel (dev) | ngrok |
| Config | python-dotenv + YAML |

---

## Project structure

```
├── agent/
│   ├── main.py          FastAPI webhook + identity routing
│   ├── brain.py         Two Claude contexts (tenant / landlord) with tool use
│   ├── memory.py        Conversation history per phone number
│   ├── repairs.py       Repair request state machine
│   ├── tools.py         Business knowledge helpers
│   ├── db.py            Shared SQLAlchemy engine
│   └── providers/
│       ├── base.py      Abstract WhatsApp provider
│       ├── twilio.py    Twilio adapter
│       └── __init__.py  Provider factory
├── config/
│   ├── prompts.yaml     System prompts (tenant + landlord)
│   ├── business.yaml    Business info (property, parties, history)
│   └── people.yaml      Phone number → identity mapping
├── knowledge/           Lease docs, charge breakdowns, past incidents
├── tests/
│   └── test_local.py    Terminal chat simulator (no WhatsApp needed)
├── .env                 API keys (never committed)
└── requirements.txt
```

---

## Setup

### Requirements

- Python 3.11+
- Anthropic API key (`sk-ant-...`)
- Twilio account (sandbox, free)
- ngrok account (free)

### Install

```bash
git clone https://github.com/cggailla/christophe.ai.git
cd christophe.ai
uv venv --python 3.11
uv pip install -r requirements.txt
```

### Configure

Fill in `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
WHATSAPP_PROVIDER=twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+14155238886
PORT=8000
ENVIRONMENT=development
DATABASE_URL=sqlite+aiosqlite:///./agentkit.db
```

Fill in `config/people.yaml` with the landlord's phone number (must have joined the Twilio sandbox):

```yaml
landlord:
  name: "Marie Dubois"
  phone: "+33..."

tenant:
  name: "Thomas Martin"
```

### Run

```bash
# Start the server
.venv/bin/uvicorn agent.main:app --reload --port 8000

# In another terminal, expose it
./ngrok http 8000
```

Set the ngrok URL + `/webhook` as the Twilio sandbox inbound URL in the Twilio console.

### Test locally (no WhatsApp needed)

```bash
.venv/bin/python tests/test_local.py
```

---

## Special commands

Send `clean` from any WhatsApp number to wipe conversation memory and reset all pending repairs. Useful for demos.

---

## Key design decisions

**Why autonomous agent + tool use instead of a state machine?**
Claude decides what to do based on context — it reads the conversation, the repair state, and the parties involved, then calls the right tool. This makes it adaptable: the same agent handles a broken boiler, a rent receipt request, or a payment delay without branching code.

**Why two separate brain contexts?**
The tenant and landlord have different roles, expectations, and available actions. Tenant brain has `contacter_bailleur()`. Landlord brain has `approuver_intervention()` and `refuser_intervention()`. Each system prompt is tuned to the party's perspective.

**Why identity by phone number?**
The same Twilio number receives messages from everyone. Routing by phone number (mapped in `people.yaml`) keeps the webhook simple and the logic clean.

---

## What's next

- Multi-tenant support (multiple properties, multiple landlords)
- Slot selection flow (tenant picks from proposed repair slots)
- Real repair company integrations (vs. simulated slots)
- Lease document generation (rent receipts, amendments)
- Charge regularization assistant

---

## License

MIT
