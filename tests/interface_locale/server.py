#!/usr/bin/env python3
# tests/interface_locale/server.py — Interface web 3 colonnes sans Twilio
# Lance avec : python tests/interface_locale/server.py
# Ouvre      : http://localhost:7777

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import yaml
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from dotenv import load_dotenv

load_dotenv(override=True)

import agent.tools_exec as tools_exec
import agent.journal as journal_module
import agent.dossiers as dossiers_module
from agent.brain import agent_loop
from agent.memory import initialiser_db, sauvegarder_message, obtenir_historique, effacer_historique
from agent.repairs import effacer_toutes_reparations
from agent.notes import effacer_toutes_notes
from agent.dossiers import effacer_tous_dossiers
from agent.journal import effacer_journal


# ── Config ────────────────────────────────────────────────────────────────────

def charger_people() -> dict:
    try:
        with open("config/people.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

people  = charger_people()
TENANT  = people.get("tenant",   {"name": "Thomas Martin", "phone": "local-thomas"})
LANDLORD = people.get("landlord", {"name": "Marie Dubois",  "phone": "local-marie"})


# ── WebSocket manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, event_type: str, data: dict):
        msg = json.dumps({"type": event_type, **data}, ensure_ascii=False)
        for ws in list(self.active):
            try:
                await ws.send_text(msg)
            except Exception:
                pass

manager = ConnectionManager()


# ── Mock fournisseur ──────────────────────────────────────────────────────────

class MockFournisseur:
    async def envoyer_message(self, phone: str, message: str) -> bool:
        if not message or not message.strip():
            await manager.broadcast("christophe_log", {
                "level": "warn",
                "text": "⚠️ Tentative d'envoi d'un message vide — ignoré",
            })
            return False

        if phone == TENANT["phone"]:
            to, to_name = "thomas", TENANT["name"]
        elif phone == LANDLORD["phone"]:
            to, to_name = "marie", LANDLORD["name"]
        else:
            to, to_name = "unknown", phone

        await manager.broadcast("outbound", {"to": to, "to_name": to_name, "text": message})
        return True

    async def envoyer_document(self, phone: str, filepath: str, legende: str = "") -> bool:
        if phone == TENANT["phone"]:
            to, to_name = "thomas", TENANT["name"]
        elif phone == LANDLORD["phone"]:
            to, to_name = "marie", LANDLORD["name"]
        else:
            to, to_name = "unknown", phone

        filename = os.path.basename(filepath)
        url = f"http://localhost:7777/documents/{filename}"
        await manager.broadcast("document", {
            "to": to, "to_name": to_name,
            "filename": filename,
            "url": url,
            "legende": legende,
        })
        return True

fournisseur = MockFournisseur()


# ── Tool broadcast hook ───────────────────────────────────────────────────────

TOOL_ICONS = {
    "recherche_web":                "🔍",
    "contacter_partie":             "📨",
    "obtenir_creneaux":             "📅",
    "confirmer_rdv":                "✅",
    "sauvegarder_note":             "💾",
    "lire_note":                    "📖",
    "creer_dossier":                "📋",
    "mettre_a_jour_milestone":      "🔄",
    "lire_dossier_actif":           "📋",
    "generer_et_envoyer_document":  "📄",
    "escalader":                    "🚨",
}

async def broadcast_tool(event_type: str, data: dict):
    tool = data.get("tool", "")
    icon = TOOL_ICONS.get(tool, "🔧")

    if event_type == "tool_call":
        inp = data.get("input", {})
        # Résumé lisible selon le tool
        if tool == "recherche_web":
            detail = f'"{inp.get("query", "")}"'
        elif tool == "contacter_partie":
            detail = f'→ {inp.get("destinataire", "")}'
        elif tool == "creer_dossier":
            detail = f'"{inp.get("titre", "")}"'
        elif tool == "mettre_a_jour_milestone":
            detail = f'{inp.get("milestone_id")} → {inp.get("statut")}'
        elif tool == "sauvegarder_note" or tool == "lire_note":
            detail = f'clé: {inp.get("cle", "")}'
        else:
            detail = ""
        await manager.broadcast("christophe_log", {
            "level": "tool",
            "text": f"{icon} {tool} {detail}".strip(),
        })

tools_exec._broadcast_hook = broadcast_tool


async def broadcast_generic(event_type: str, data: dict):
    await manager.broadcast(event_type, data)

journal_module._broadcast_hook = broadcast_generic
dossiers_module._broadcast_hook = broadcast_generic


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_ctx(speaker: str, phone: str) -> dict:
    return {
        "speaker":        speaker,
        "speaker_name":   LANDLORD["name"] if speaker == "bailleur" else TENANT["name"],
        "speaker_phone":  phone,
        "tenant_name":    TENANT["name"],
        "tenant_phone":   TENANT["phone"],
        "landlord_name":  LANDLORD["name"],
        "landlord_phone": LANDLORD["phone"],
        "fournisseur":    fournisseur,
    }


async def run_agent(speaker: str, phone: str, speaker_key: str, text: str):
    await manager.broadcast("thinking", {"status": "start", "for": speaker_key})
    try:
        ctx = build_ctx(speaker, phone)
        historique = await obtenir_historique(phone)
        reponse = await agent_loop(text, historique, ctx)
        await sauvegarder_message(phone, "user", text)
        await sauvegarder_message(phone, "assistant", reponse)
        await manager.broadcast("reply", {
            "to": speaker_key,
            "to_name": TENANT["name"] if speaker_key == "thomas" else LANDLORD["name"],
            "text": reponse,
        })
    except Exception as e:
        await manager.broadcast("christophe_log", {"level": "error", "text": f"❌ Erreur : {e}"})
    finally:
        await manager.broadcast("thinking", {"status": "done", "for": speaker_key})


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Christophe.AI — Interface locale")


@app.on_event("startup")
async def startup():
    await initialiser_db()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.post("/send")
async def send(body: dict):
    speaker_key = body.get("speaker", "thomas")
    text = body.get("text", "").strip()
    if not text:
        return {"ok": False}

    if speaker_key == "thomas":
        speaker, phone = "locataire", TENANT["phone"]
        from_name = TENANT["name"]
    else:
        speaker, phone = "bailleur", LANDLORD["phone"]
        from_name = LANDLORD["name"]

    await manager.broadcast("message_in", {
        "from": speaker_key,
        "from_name": from_name,
        "text": text,
    })
    asyncio.create_task(run_agent(speaker, phone, speaker_key, text))
    return {"ok": True}


@app.post("/clean")
async def clean():
    await effacer_historique(TENANT["phone"])
    await effacer_historique(LANDLORD["phone"])
    await effacer_toutes_reparations()
    await effacer_toutes_notes()
    await effacer_tous_dossiers()
    await effacer_journal()
    await manager.broadcast("clean", {})
    return {"ok": True}


@app.get("/documents/{filename}")
async def servir_document(filename: str):
    from fastapi import HTTPException
    from pathlib import Path

    # Refuser tout caractère de chemin pour bloquer le path traversal
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide")

    base = Path("documents").resolve()
    resolved = (base / filename).resolve()
    if not str(resolved).startswith(str(base) + os.sep) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Document introuvable")

    return FileResponse(str(resolved), media_type="application/pdf", filename=filename)


@app.get("/")
async def ui():
    return HTMLResponse(HTML)


# ── Interface HTML ────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Christophe.AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --thomas: #2563eb;
    --thomas-light: #dbeafe;
    --marie: #059669;
    --marie-light: #d1fae5;
    --agent-bg: #f0f2f5;
    --bubble-in: #ffffff;
    --border: #e5e7eb;
    --text-primary: #111827;
    --text-secondary: #6b7280;
    --text-muted: #9ca3af;
  }

  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #e5ddd5;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ─── TOP BAR ─────────────────────────────────────────── */
  .topbar {
    background: #fff;
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
    z-index: 10;
  }
  .topbar-brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .topbar-logo {
    width: 32px; height: 32px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
  }
  .topbar-title { font-size: 15px; font-weight: 700; color: var(--text-primary); }
  .topbar-sub   { font-size: 11px; color: var(--text-muted); margin-top: 1px; }
  .btn-reset {
    background: #fff;
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 6px 16px;
    border-radius: 20px;
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
    font-weight: 500;
    display: flex; align-items: center; gap: 6px;
    transition: all 0.15s;
  }
  .btn-reset:hover { background: #fef2f2; border-color: #fca5a5; color: #ef4444; }

  /* ─── MAIN LAYOUT ──────────────────────────────────────── */
  .layout {
    display: grid;
    grid-template-columns: 1fr 360px 1fr;
    gap: 0;
    flex: 1;
    overflow: hidden;
  }

  /* ─── CHAT COLUMN ──────────────────────────────────────── */
  .chat-col {
    display: flex;
    flex-direction: column;
    background: var(--agent-bg);
    overflow: hidden;
    border-right: 1px solid var(--border);
  }
  .chat-col:last-child { border-right: none; border-left: 1px solid var(--border); }

  /* Chat header */
  .chat-header {
    background: #fff;
    padding: 12px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .avatar {
    width: 40px; height: 40px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; font-weight: 700; color: #fff;
    flex-shrink: 0;
  }
  .avatar.thomas { background: linear-gradient(135deg, #2563eb, #60a5fa); }
  .avatar.marie  { background: linear-gradient(135deg, #059669, #34d399); }
  .chat-header-info .name  { font-size: 14px; font-weight: 600; color: var(--text-primary); }
  .chat-header-info .role  { font-size: 11px; color: var(--text-muted); margin-top: 1px; }
  .status-pill {
    margin-left: auto;
    font-size: 10px;
    font-weight: 600;
    padding: 3px 9px;
    border-radius: 20px;
    letter-spacing: 0.3px;
  }
  .status-pill.idle    { background: #f3f4f6; color: #9ca3af; }
  .status-pill.active  { background: #ede9fe; color: #7c3aed; }

  /* Chat messages */
  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .chat-messages::-webkit-scrollbar { width: 4px; }
  .chat-messages::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.15); border-radius: 4px; }

  /* Bubbles */
  .msg-row { display: flex; margin-bottom: 2px; }
  .msg-row.sent { justify-content: flex-end; }
  .msg-row.recv { justify-content: flex-start; }

  .bubble {
    max-width: 80%;
    padding: 8px 12px 6px;
    border-radius: 18px;
    font-size: 13.5px;
    line-height: 1.5;
    word-break: break-word;
    white-space: pre-wrap;
    position: relative;
    box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  }
  .bubble.sent {
    background: var(--thomas);
    color: #fff;
    border-bottom-right-radius: 4px;
  }
  .chat-col.col-marie .bubble.sent {
    background: var(--marie);
  }
  .bubble.recv {
    background: #fff;
    color: var(--text-primary);
    border-bottom-left-radius: 4px;
  }
  .doc-bubble { cursor: pointer; border: 1px solid #bfdbfe !important; background: #f0f7ff !important; }
  .doc-bubble:hover { background: #e0f0ff !important; }

  .bubble .btime {
    font-size: 10px;
    opacity: 0.6;
    margin-top: 3px;
    text-align: right;
  }
  .bubble.recv .btime { color: var(--text-muted); }

  /* Typing indicator in chat */
  .typing-bubble {
    background: #fff;
    border-radius: 18px;
    border-bottom-left-radius: 4px;
    padding: 10px 16px;
    display: none;
    box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  }
  .typing-bubble.show { display: flex; gap: 4px; align-items: center; }
  .typing-bubble span {
    width: 7px; height: 7px;
    background: #9ca3af;
    border-radius: 50%;
    animation: typingBounce 1.2s infinite ease-in-out;
  }
  .typing-bubble span:nth-child(2) { animation-delay: 0.2s; }
  .typing-bubble span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes typingBounce {
    0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
    40%           { transform: translateY(-5px); opacity: 1; }
  }

  /* Chat input */
  .chat-input-wrap {
    padding: 10px 12px;
    background: var(--agent-bg);
    border-top: 1px solid rgba(0,0,0,0.06);
    display: flex;
    gap: 8px;
    align-items: flex-end;
    flex-shrink: 0;
  }
  .chat-input-wrap textarea {
    flex: 1;
    background: #fff;
    border: none;
    border-radius: 22px;
    padding: 10px 16px;
    font-size: 14px;
    font-family: inherit;
    color: var(--text-primary);
    resize: none;
    outline: none;
    line-height: 1.4;
    max-height: 120px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
  .chat-input-wrap textarea::placeholder { color: var(--text-muted); }
  .btn-send {
    width: 42px; height: 42px;
    border-radius: 50%;
    border: none;
    cursor: pointer;
    font-size: 18px;
    color: #fff;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    transition: transform 0.15s, opacity 0.15s;
  }
  .btn-send:hover   { transform: scale(1.05); }
  .btn-send:active  { transform: scale(0.95); }
  .btn-send:disabled { opacity: 0.35; cursor: not-allowed; transform: none; }
  .col-thomas .btn-send { background: var(--thomas); }
  .col-marie  .btn-send { background: var(--marie); }

  /* ─── CHRISTOPHE CENTER ────────────────────────────────── */
  .agent-col {
    display: flex;
    flex-direction: column;
    background: #fafafa;
    overflow: hidden;
  }

  .agent-header {
    padding: 14px 16px 12px;
    border-bottom: 1px solid var(--border);
    background: #fff;
    flex-shrink: 0;
  }
  .agent-header-top {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  .agent-logo {
    width: 34px; height: 34px;
    border-radius: 10px;
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; flex-shrink: 0;
  }
  .agent-name { font-size: 14px; font-weight: 700; color: var(--text-primary); }
  .agent-desc { font-size: 11px; color: var(--text-muted); margin-top: 1px; }

  /* Pipeline bar */
  .pipeline { display: flex; gap: 4px; }
  .pipeline-step {
    flex: 1; height: 3px; border-radius: 4px;
    background: #e5e7eb; transition: background 0.4s;
  }
  .pipeline-step.done   { background: #6366f1; }
  .pipeline-step.active { background: linear-gradient(90deg,#6366f1,#a78bfa); animation: shimmer 1.5s infinite; }
  @keyframes shimmer { 0%,100%{opacity:1} 50%{opacity:.6} }
  .pipeline-labels { display:flex; gap:4px; margin-top:4px; }
  .pipeline-label {
    flex:1; font-size:9px; color:var(--text-muted);
    text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }

  /* ─── DOSSIER PANEL ───────────────────────────────────── */
  .dossier-panel {
    background: #fff;
    border-bottom: 1px solid var(--border);
    padding: 10px 14px;
    flex-shrink: 0;
    display: none;
  }
  .dossier-panel.visible { display: block; }
  .dossier-title {
    font-size: 11px; font-weight: 700; color: #6366f1;
    text-transform: uppercase; letter-spacing: 0.5px;
    margin-bottom: 8px;
    display: flex; align-items: center; gap: 6px;
  }
  .dossier-title-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #6366f1; animation: pulse 2s infinite;
  }
  .milestones-list { display: flex; flex-direction: column; gap: 4px; }
  .milestone-row {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 8px; border-radius: 7px;
    background: #f9fafb; font-size: 12px;
    transition: background 0.3s;
  }
  .milestone-row.done   { background: #f0fdf4; }
  .milestone-row.active { background: #f5f3ff; }
  .milestone-icon { font-size: 13px; flex-shrink: 0; width: 18px; text-align: center; }
  .milestone-label { color: var(--text-secondary); flex: 1; }
  .milestone-row.done .milestone-label   { color: #16a34a; text-decoration: line-through; opacity: 0.7; }
  .milestone-row.active .milestone-label { color: #6366f1; font-weight: 600; }
  .milestone-id { font-size: 9px; color: var(--text-muted); flex-shrink: 0; }

  /* ─── ACTIVITY FEED ───────────────────────────────────── */
  .agent-feed {
    flex: 1; overflow-y: auto;
    padding: 12px 10px;
    display: flex; flex-direction: column; gap: 5px;
    min-height: 0;
  }
  .agent-feed::-webkit-scrollbar { width: 3px; }
  .agent-feed::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 4px; }

  .activity-card {
    border-radius: 9px; padding: 8px 11px;
    font-size: 12px; line-height: 1.5; word-break: break-word;
    animation: slideIn 0.2s ease-out;
    display: flex; align-items: flex-start; gap: 8px;
  }
  @keyframes slideIn { from{opacity:0;transform:translateY(5px)} to{opacity:1;transform:translateY(0)} }
  .activity-icon { font-size: 13px; flex-shrink: 0; margin-top: 1px; }
  .activity-body { flex: 1; min-width: 0; }
  .activity-label { font-weight: 600; color: var(--text-primary); font-size: 11px; margin-bottom: 1px; }
  .activity-detail { color: var(--text-secondary); font-size: 11px; word-break: break-word; white-space: pre-wrap; }
  .activity-time  { font-size: 9px; color: var(--text-muted); margin-top: 2px; }

  .card-tool     { background:#f5f3ff; border:1px solid #e0d9ff; }
  .card-msg-in   { background:#fff;    border:1px solid var(--border); }
  .card-thomas   { background:#eff6ff; border:1px solid #bfdbfe; }
  .card-marie    { background:#ecfdf5; border:1px solid #a7f3d0; }
  .card-error    { background:#fef2f2; border:1px solid #fecaca; }
  .card-thinking { background:linear-gradient(135deg,#f5f3ff,#faf5ff); border:1px solid #e0d9ff; align-items:center; }
  .think-dots { display:flex; gap:4px; }
  .think-dots span {
    width:6px; height:6px; border-radius:50%; background:#7c3aed;
    animation:typingBounce 1.2s infinite ease-in-out;
  }
  .think-dots span:nth-child(2){animation-delay:.2s}
  .think-dots span:nth-child(3){animation-delay:.4s}

  /* ─── JOURNAL PANEL ───────────────────────────────────── */
  .journal-panel {
    flex-shrink: 0;
    border-top: 1px solid var(--border);
    background: #fff;
    display: flex; flex-direction: column;
    max-height: 200px; min-height: 90px;
  }
  .journal-header {
    padding: 7px 14px 5px;
    display: flex; align-items: center; justify-content: space-between;
    flex-shrink: 0;
  }
  .journal-header-title {
    font-size: 10px; font-weight: 700; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.6px;
    display: flex; align-items: center; gap: 5px;
  }
  .journal-dot {
    width: 5px; height: 5px; border-radius: 50%; background: #10b981;
  }
  .journal-count {
    font-size: 9px; background: #f3f4f6; color: #6b7280;
    padding: 1px 6px; border-radius: 10px;
  }
  .journal-list {
    flex: 1; overflow-y: auto;
    padding: 0 14px 8px;
    display: flex; flex-direction: column; gap: 2px;
  }
  .journal-list::-webkit-scrollbar { width: 3px; }
  .journal-list::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 4px; }
  .journal-entry {
    display: flex; align-items: baseline; gap: 8px;
    padding: 3px 0; font-size: 11px;
    border-bottom: 1px solid #f9fafb;
    animation: slideIn 0.2s ease-out;
  }
  .journal-entry:last-child { border-bottom: none; }
  .journal-time { color: var(--text-muted); font-size: 9px; flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .journal-desc { color: var(--text-secondary); flex: 1; word-break: break-word; }
  .journal-empty { color: var(--text-muted); font-size: 11px; font-style: italic; padding: 6px 0; }

  /* Empty state */
  .feed-empty {
    flex:1; display:flex; flex-direction:column;
    align-items:center; justify-content:center;
    gap:10px; color:var(--text-muted); padding:30px 20px; text-align:center;
  }
  .feed-empty .icon { font-size:32px; opacity:.25; }
  .feed-empty p { font-size:11px; line-height:1.6; max-width:190px; }
</style>
</head>
<body>

<!-- TOP BAR -->
<div class="topbar">
  <div class="topbar-brand">
    <div class="topbar-logo">🏠</div>
    <div>
      <div class="topbar-title">Christophe.AI</div>
      <div class="topbar-sub">Simulateur local · Studio 42 rue de la Roquette, Paris 11</div>
    </div>
  </div>
  <button class="btn-reset" onclick="hardClean()">
    <span>↺</span> Nouvelle conversation
  </button>
</div>

<!-- LAYOUT -->
<div class="layout">

  <!-- THOMAS -->
  <div class="chat-col col-thomas">
    <div class="chat-header">
      <div class="avatar thomas">T</div>
      <div class="chat-header-info">
        <div class="name">Thomas Martin</div>
        <div class="role">Locataire</div>
      </div>
      <div class="status-pill idle" id="pill-thomas">En ligne</div>
    </div>
    <div class="chat-messages" id="messages-thomas">
      <div class="feed-empty">
        <div class="icon">💬</div>
        <p>Envoie un premier message en tant que Thomas</p>
      </div>
    </div>
    <div class="msg-row recv" id="typing-thomas" style="display:none; padding: 0 12px 8px;">
      <div class="typing-bubble show"><span></span><span></span><span></span></div>
    </div>
    <div class="chat-input-wrap">
      <textarea id="input-thomas" placeholder="Message…" rows="1"
        onkeydown="handleKey(event,'thomas')"></textarea>
      <button class="btn-send" id="btn-thomas" onclick="sendMsg('thomas')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
      </button>
    </div>
  </div>

  <!-- CHRISTOPHE -->
  <div class="agent-col">

    <!-- Header + pipeline -->
    <div class="agent-header">
      <div class="agent-header-top">
        <div class="agent-logo">✦</div>
        <div>
          <div class="agent-name">Christophe.AI</div>
          <div class="agent-desc">Agent immobilier autonome</div>
        </div>
      </div>
      <div class="pipeline" id="pipeline">
        <div class="pipeline-step"></div>
        <div class="pipeline-step"></div>
        <div class="pipeline-step"></div>
        <div class="pipeline-step"></div>
        <div class="pipeline-step"></div>
      </div>
      <div class="pipeline-labels">
        <div class="pipeline-label">Réception</div>
        <div class="pipeline-label">Analyse</div>
        <div class="pipeline-label">Action</div>
        <div class="pipeline-label">Contact</div>
        <div class="pipeline-label">Réponse</div>
      </div>
    </div>

    <!-- Dossiers actifs (peuvent coexister) -->
    <div class="dossier-panel" id="dossier-panel">
      <div id="dossiers-container"></div>
    </div>

    <!-- Activité temps réel -->
    <div class="agent-feed" id="agent-feed">
      <div class="feed-empty" id="feed-empty">
        <div class="icon">✦</div>
        <p>L'activité de Christophe apparaîtra ici en temps réel</p>
      </div>
    </div>

    <!-- Journal permanent -->
    <div class="journal-panel">
      <div class="journal-header">
        <div class="journal-header-title">
          <div class="journal-dot"></div>
          Journal des actions
        </div>
        <div class="journal-count" id="journal-count">0</div>
      </div>
      <div class="journal-list" id="journal-list">
        <div class="journal-empty">Aucune action encore enregistrée</div>
      </div>
    </div>

  </div>

  <!-- MARIE -->
  <div class="chat-col col-marie">
    <div class="chat-header">
      <div class="avatar marie">M</div>
      <div class="chat-header-info">
        <div class="name">Marie Dubois</div>
        <div class="role">Bailleur · Propriétaire</div>
      </div>
      <div class="status-pill idle" id="pill-marie">En ligne</div>
    </div>
    <div class="chat-messages" id="messages-marie">
      <div class="feed-empty">
        <div class="icon">💬</div>
        <p>Envoie un premier message en tant que Marie</p>
      </div>
    </div>
    <div class="msg-row recv" id="typing-marie" style="display:none; padding: 0 12px 8px;">
      <div class="typing-bubble show"><span></span><span></span><span></span></div>
    </div>
    <div class="chat-input-wrap">
      <textarea id="input-marie" placeholder="Message…" rows="1"
        onkeydown="handleKey(event,'marie')"></textarea>
      <button class="btn-send" id="btn-marie" onclick="sendMsg('marie')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
      </button>
    </div>
  </div>

</div>

<script>
const ws = new WebSocket(`ws://${location.host}/ws`);
let thinkingCard = null;
let pipelineStep = 0;
let journalCount = 0;

const TOOL_META = {
  recherche_web:          { icon: '🔍', label: 'Recherche web' },
  contacter_partie:       { icon: '📨', label: 'Envoi message' },
  obtenir_creneaux:       { icon: '📅', label: 'Créneaux disponibles' },
  confirmer_rdv:          { icon: '✅', label: 'Confirmation RDV' },
  sauvegarder_note:       { icon: '💾', label: 'Mémorisation' },
  lire_note:              { icon: '📖', label: 'Lecture note' },
  creer_dossier:          { icon: '📋', label: 'Création dossier' },
  mettre_a_jour_milestone:{ icon: '🔄', label: 'Mise à jour dossier' },
  lire_dossier_actif:     { icon: '📋', label: 'Lecture dossier' },
  escalader:              { icon: '🚨', label: 'Escalade' },
};

function now() {
  return new Date().toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit'});
}

function clearEmpty(id) {
  const el = document.getElementById(id);
  const emp = el.querySelector('.feed-empty');
  if (emp) emp.remove();
}

function addBubble(side, text, type) {
  clearEmpty(`messages-${side}`);
  const container = document.getElementById(`messages-${side}`);
  const row = document.createElement('div');
  row.className = `msg-row ${type}`;
  const bub = document.createElement('div');
  bub.className = `bubble ${type}`;
  bub.textContent = text;
  const t = document.createElement('div');
  t.className = 'btime';
  t.textContent = now();
  bub.appendChild(t);
  row.appendChild(bub);
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

function addCard(icon, label, detail, variant) {
  const feed = document.getElementById('agent-feed');
  clearEmpty('agent-feed');
  document.getElementById('feed-empty')?.remove();

  const card = document.createElement('div');
  card.className = `activity-card ${variant}`;
  card.innerHTML = `
    <div class="activity-icon">${icon}</div>
    <div class="activity-body">
      <div class="activity-label">${label}</div>
      ${detail ? `<div class="activity-detail">${escHtml(detail)}</div>` : ''}
      <div class="activity-time">${now()}</div>
    </div>`;
  feed.appendChild(card);
  feed.scrollTop = feed.scrollHeight;
  return card;
}

// ── Journal ──────────────────────────────────────────────
function addJournalEntry(time, desc) {
  const list = document.getElementById('journal-list');
  const empty = list.querySelector('.journal-empty');
  if (empty) empty.remove();

  const row = document.createElement('div');
  row.className = 'journal-entry';
  row.innerHTML = `<span class="journal-time">${time}</span><span class="journal-desc">${escHtml(desc)}</span>`;
  list.appendChild(row);
  list.scrollTop = list.scrollHeight;

  journalCount++;
  document.getElementById('journal-count').textContent = journalCount;
}

// ── Dossier ───────────────────────────────────────────────
const MILESTONE_ICONS = {
  FAIT: '✅', EN_COURS: '🔄', EN_ATTENTE: '⏳', IGNORE: '⏭️'
};
// État local : tous les dossiers actifs vus par l'UI
const activeDossiers = new Map();

function renderDossiers() {
  const panel = document.getElementById('dossier-panel');
  const container = document.getElementById('dossiers-container');
  container.innerHTML = '';

  const visibles = [...activeDossiers.values()].filter(d => d.statut === 'ACTIF');
  if (visibles.length === 0) {
    panel.classList.remove('visible');
    return;
  }
  panel.classList.add('visible');

  visibles.forEach(d => {
    const block = document.createElement('div');
    block.style.cssText = 'margin-bottom: 10px;';
    const milestonesHtml = (d.milestones || []).map(m => {
      const cls = m.statut === 'FAIT' ? 'done' : m.statut === 'EN_COURS' ? 'active' : '';
      const icon = MILESTONE_ICONS[m.statut] || '⏳';
      return `<div class="milestone-row ${cls}">
        <span class="milestone-icon">${icon}</span>
        <span class="milestone-label">${escHtml(m.label)}</span>
        <span class="milestone-id">${m.id}</span></div>`;
    }).join('');
    block.innerHTML = `
      <div class="dossier-title">
        <div class="dossier-title-dot"></div>
        <span>#${d.id} · ${escHtml(d.titre)}</span>
      </div>
      <div class="milestones-list">${milestonesHtml}</div>`;
    container.appendChild(block);
  });
}

function updateDossier(dossier) {
  if (!dossier) return;
  if (dossier.statut === 'TERMINE' || dossier.statut === 'ANNULE') {
    activeDossiers.delete(dossier.id);
  } else {
    activeDossiers.set(dossier.id, dossier);
  }
  renderDossiers();
}

function addDocBubble(side, filename, url, legende) {
  clearEmpty(`messages-${side}`);
  const container = document.getElementById(`messages-${side}`);
  const row = document.createElement('div');
  row.className = 'msg-row recv';
  row.innerHTML = `
    <a href="${url}" target="_blank" style="text-decoration:none;">
      <div class="bubble recv doc-bubble">
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="font-size:28px;flex-shrink:0;">📄</div>
          <div>
            <div style="font-weight:600;font-size:13px;color:#1d4ed8;">${filename}</div>
            <div style="font-size:11px;color:#6b7280;margin-top:2px;">${legende || 'Document joint'}</div>
            <div style="font-size:10px;color:#2563eb;margin-top:4px;font-weight:500;">Ouvrir le PDF →</div>
          </div>
        </div>
        <div class="btime">${now()}</div>
      </div>
    </a>`;
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function advancePipeline(step) {
  document.querySelectorAll('.pipeline-step').forEach((el, i) => {
    el.classList.remove('active', 'done');
    if (i < step)      el.classList.add('done');
    else if (i === step) el.classList.add('active');
  });
  pipelineStep = step;
}

ws.onmessage = (e) => {
  const ev = JSON.parse(e.data);

  if (ev.type === 'message_in') {
    addBubble(ev.from, ev.text, 'sent');
    addCard('💬', `Message de ${ev.from_name}`, ev.text.length > 80 ? ev.text.slice(0,80)+'…' : ev.text, 'card-msg-in');
    advancePipeline(0);
  }

  if (ev.type === 'thinking') {
    const pill  = document.getElementById(`pill-${ev.for}`);
    const typing = document.getElementById(`typing-${ev.for}`);
    if (ev.status === 'start') {
      pill.textContent = 'En traitement…';
      pill.className = 'status-pill active';
      typing.style.display = 'flex';
      advancePipeline(1);
      // Carte "thinking" persistante
      thinkingCard = addCard('✦', 'Christophe réfléchit', null, 'card-thinking');
      thinkingCard.querySelector('.activity-body').innerHTML =
        `<div class="activity-label" style="color:#7c3aed">Christophe réfléchit…</div>
         <div class="think-dots"><span></span><span></span><span></span></div>`;
    } else {
      pill.textContent = 'En ligne';
      pill.className = 'status-pill idle';
      typing.style.display = 'none';
      if (thinkingCard) { thinkingCard.remove(); thinkingCard = null; }
    }
  }

  if (ev.type === 'tool_call') {
    const tool = ev.input?.tool || '';
    const meta = TOOL_META[ev.tool] || { icon: '🔧', label: ev.tool };
    let detail = '';
    const inp = ev.input || {};
    if (ev.tool === 'recherche_web')           detail = inp.query || '';
    else if (ev.tool === 'contacter_partie')   detail = `→ ${inp.destinataire}`;
    else if (ev.tool === 'creer_dossier')      detail = inp.titre || '';
    else if (ev.tool === 'mettre_a_jour_milestone') detail = `${inp.milestone_id} → ${inp.statut}`;
    else if (ev.tool === 'sauvegarder_note' || ev.tool === 'lire_note') detail = `clé : ${inp.cle}`;
    addCard(meta.icon, meta.label, detail, 'card-tool');
    advancePipeline(2);
  }

  if (ev.type === 'christophe_log') {
    if (ev.level === 'error') addCard('❌', 'Erreur', ev.text, 'card-error');
    else if (ev.level === 'warn') addCard('⚠️', 'Attention', ev.text, 'card-system');
    // ignore other system logs (already shown via tool_call)
  }

  if (ev.type === 'outbound') {
    const cls   = ev.to === 'thomas' ? 'card-thomas' : 'card-marie';
    const preview = ev.text.length > 100 ? ev.text.slice(0,100)+'…' : ev.text;
    addCard('📨', `Envoyé à ${ev.to_name}`, preview, cls);
    addBubble(ev.to, ev.text, 'recv');
    advancePipeline(3);
  }

  if (ev.type === 'document') {
    const cls = ev.to === 'thomas' ? 'card-thomas' : 'card-marie';
    addCard('📄', `Document → ${ev.to_name}`, ev.filename, cls);
    addDocBubble(ev.to, ev.filename, ev.url, ev.legende);
    advancePipeline(3);
  }

  if (ev.type === 'reply') {
    const cls = ev.to === 'thomas' ? 'card-thomas' : 'card-marie';
    addCard('✦', `Réponse à ${ev.to_name}`, null, cls);
    addBubble(ev.to, ev.text, 'recv');
    advancePipeline(4);
    setTimeout(() => advancePipeline(-1), 1500);
  }

  if (ev.type === 'journal_entry') {
    addJournalEntry(ev.time, ev.desc);
  }

  if (ev.type === 'dossier_update') {
    updateDossier(ev.dossier);
  }

  if (ev.type === 'clean') {
    ['thomas','marie'].forEach(s => {
      document.getElementById(`messages-${s}`).innerHTML =
        `<div class="feed-empty"><div class="icon">💬</div><p>Nouvelle conversation</p></div>`;
    });
    document.getElementById('agent-feed').innerHTML =
      `<div class="feed-empty" id="feed-empty"><div class="icon">✦</div><p>L'activité de Christophe apparaîtra ici en temps réel</p></div>`;
    document.querySelectorAll('.pipeline-step').forEach(el => el.className = 'pipeline-step');
    pipelineStep = 0;
    // Reset dossier panel
    activeDossiers.clear();
    renderDossiers();
    // Reset journal
    document.getElementById('journal-list').innerHTML = '<div class="journal-empty">Aucune action encore enregistrée</div>';
    document.getElementById('journal-count').textContent = '0';
    journalCount = 0;
  }
};

async function sendMsg(speaker) {
  const input = document.getElementById(`input-${speaker}`);
  const btn   = document.getElementById(`btn-${speaker}`);
  const text  = input.value.trim();
  if (!text) return;
  input.value = '';
  input.style.height = 'auto';
  btn.disabled = true;
  await fetch('/send', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({speaker, text}),
  });
  btn.disabled = false;
  input.focus();
}

async function hardClean() {
  await fetch('/clean', {method:'POST'});
}

function handleKey(e, speaker) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMsg(speaker);
  }
  e.target.style.height = 'auto';
  e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
}
</script>
</body>
</html>
"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  Christophe.AI — Interface locale")
    print("=" * 55)
    print(f"  Thomas  : {TENANT['name']} ({TENANT['phone']})")
    print(f"  Marie   : {LANDLORD['name']} ({LANDLORD['phone']})")
    print(f"  URL     : http://localhost:7777")
    print("=" * 55)
    print()
    uvicorn.run(app, host="0.0.0.0", port=7777, log_level="warning")
