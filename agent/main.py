# agent/main.py — Routing + agent loop unifié

import os
import yaml
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import agent_loop
from agent.memory import initialiser_db, sauvegarder_message, obtenir_historique
from agent.providers import obtenir_fournisseur

load_dotenv(override=True)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("christophe")

fournisseur = obtenir_fournisseur()
PORT = int(os.getenv("PORT", 8000))


def charger_people() -> dict:
    try:
        with open("config/people.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


PEOPLE = charger_people()
LANDLORD_PHONE = PEOPLE.get("landlord", {}).get("phone", "")
LANDLORD_NAME  = PEOPLE.get("landlord", {}).get("name", "Marie")
TENANT_PHONE   = PEOPLE.get("tenant", {}).get("phone", "")
TENANT_NAME    = PEOPLE.get("tenant", {}).get("name", "Thomas")


def build_ctx(speaker: str, speaker_phone: str) -> dict:
    return {
        "speaker":       speaker,
        "speaker_name":  LANDLORD_NAME if speaker == "bailleur" else TENANT_NAME,
        "speaker_phone": speaker_phone,
        "tenant_name":   TENANT_NAME,
        "tenant_phone":  TENANT_PHONE,
        "landlord_name": LANDLORD_NAME,
        "landlord_phone": LANDLORD_PHONE,
        "fournisseur":   fournisseur,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialiser_db()
    logger.info(f"Christophe v3 démarré — tenant: {TENANT_PHONE} / landlord: {LANDLORD_PHONE}")
    yield


app = FastAPI(title="Christophe.AI", version="3.0.0", lifespan=lifespan)

# Lock par téléphone — sérialise agent_loop pour une même conversation
# Empêche deux messages back-to-back ou un retry Twilio de lancer deux loops concurrents.
_locks_telephone: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@app.get("/")
async def health_check():
    return {"status": "ok", "agent": "Christophe", "version": "3.0"}


@app.get("/documents/{filename}")
async def servir_document(filename: str):
    """Sert les PDFs générés. Twilio fetch cette URL via MediaUrl."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from pathlib import Path

    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide")

    base = Path("documents").resolve()
    resolved = (base / filename).resolve()
    if not str(resolved).startswith(str(base) + os.sep) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Document introuvable")

    return FileResponse(str(resolved), media_type="application/pdf", filename=filename)


@app.get("/webhook")
async def webhook_verification(request: Request):
    resultat = await fournisseur.valider_webhook(request)
    if resultat is not None:
        return PlainTextResponse(str(resultat))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        messages = await fournisseur.parser_webhook(request)

        for msg in messages:
            if msg.est_propre or not msg.texte:
                continue

            # Filtre de sécurité : on n'accepte que les numéros connus.
            # Empêche une signature Twilio forgée (via une faille externe) de
            # piloter l'agent depuis un numéro arbitraire.
            if msg.telephone not in (TENANT_PHONE, LANDLORD_PHONE):
                logger.warning(f"Message reçu d'un numéro inconnu {msg.telephone} — ignoré")
                continue

            est_bailleur = msg.telephone == LANDLORD_PHONE
            speaker = "bailleur" if est_bailleur else "locataire"
            logger.info(f"[{speaker.upper()}] {msg.telephone}: {msg.texte}")

            ctx = build_ctx(speaker, msg.telephone)

            # Lock par téléphone — sérialise les agent_loops d'une même conversation.
            async with _locks_telephone[msg.telephone]:
                historique = await obtenir_historique(msg.telephone)
                reponse = await agent_loop(msg.texte, historique, ctx)
                await sauvegarder_message(msg.telephone, "user", msg.texte)
                await sauvegarder_message(msg.telephone, "assistant", reponse)
            await fournisseur.envoyer_message(msg.telephone, reponse)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Erreur webhook : {e}")
        raise HTTPException(status_code=500, detail=str(e))
