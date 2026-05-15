# agent/main.py — Routing + agent loop unifié

import os
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import agent_loop
from agent.memory import initialiser_db, sauvegarder_message, obtenir_historique, effacer_historique
from agent.repairs import effacer_toutes_reparations
from agent.notes import effacer_toutes_notes
from agent.dossiers import effacer_tous_dossiers
from agent.journal import effacer_journal
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


@app.get("/")
async def health_check():
    return {"status": "ok", "agent": "Christophe", "version": "3.0"}


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

            est_bailleur = bool(LANDLORD_PHONE and msg.telephone == LANDLORD_PHONE)
            speaker = "bailleur" if est_bailleur else "locataire"
            logger.info(f"[{speaker.upper()}] {msg.telephone}: {msg.texte}")

            # Commande spéciale : reset complet
            if msg.texte.strip().lower() == "clean":
                # Effacer les historiques des deux parties
                await effacer_historique(TENANT_PHONE)
                await effacer_historique(LANDLORD_PHONE)
                # Effacer tous les artefacts
                await effacer_toutes_reparations()
                await effacer_toutes_notes()
                await effacer_tous_dossiers()
                await effacer_journal()
                logger.info(f"[CLEAN] Reset complet déclenché par {msg.telephone}")
                await fournisseur.envoyer_message(msg.telephone, "Mémoire effacée — historique, notes, dossiers et réparations. Nouvelle conversation !")
                continue

            ctx = build_ctx(speaker, msg.telephone)
            historique = await obtenir_historique(msg.telephone)

            reponse = await agent_loop(msg.texte, historique, ctx)

            await sauvegarder_message(msg.telephone, "user", msg.texte)
            await sauvegarder_message(msg.telephone, "assistant", reponse)
            await fournisseur.envoyer_message(msg.telephone, reponse)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Erreur webhook : {e}")
        raise HTTPException(status_code=500, detail=str(e))
