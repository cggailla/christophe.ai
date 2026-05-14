# agent/main.py — Serveur FastAPI + routing locataire / bailleur

import os
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generer_reponse_tenant, generer_reponse_bailleur
from agent.memory import initialiser_db, sauvegarder_message, obtenir_historique, effacer_historique
from agent.repairs import creer_reparation, obtenir_reparation_en_attente, mettre_a_jour_statut, effacer_toutes_reparations
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
LANDLORD_NAME = PEOPLE.get("landlord", {}).get("name", "Marie")
TENANT_NAME = PEOPLE.get("tenant", {}).get("name", "Thomas")

CRENEAUX = [
    "1️⃣ Plomberie Bastille — Lundi matin 9h-11h",
    "2️⃣ ChauffeRapide Paris — Lundi après-midi 14h-16h",
    "3️⃣ ClimatPlus 75 — Mardi matin 10h-12h",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialiser_db()
    logger.info(f"Christophe démarré — landlord: {LANDLORD_PHONE}")
    yield


app = FastAPI(title="Christophe.AI", version="2.0.0", lifespan=lifespan)


@app.get("/")
async def health_check():
    return {"status": "ok", "agent": "Christophe", "version": "2.0"}


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

            est_bailleur = (LANDLORD_PHONE and msg.telephone == LANDLORD_PHONE)

            logger.info(f"{'[BAILLEUR]' if est_bailleur else '[LOCATAIRE]'} {msg.telephone}: {msg.texte}")

            if msg.texte.strip().lower() == "clean":
                await handle_clean(msg.telephone)
            elif est_bailleur:
                await handle_bailleur(msg)
            else:
                await handle_tenant(msg)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Erreur webhook : {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def handle_clean(telephone: str):
    await effacer_historique(telephone)
    await effacer_toutes_reparations()
    await fournisseur.envoyer_message(telephone, "🧹 Mémoire effacée. Nouvelle conversation !")
    logger.info(f"Clean déclenché par {telephone}")


async def handle_tenant(msg):
    historique = await obtenir_historique(msg.telephone)

    # Charger la réparation en attente pour ce locataire
    repair_en_attente = await obtenir_reparation_en_attente("WAITING_LANDLORD")
    if repair_en_attente and repair_en_attente.tenant_phone != msg.telephone:
        repair_en_attente = None  # réparation d'un autre locataire, ignorer

    reponse, action = await generer_reponse_tenant(msg.texte, historique, repair_en_attente)

    await sauvegarder_message(msg.telephone, "user", msg.texte)
    await sauvegarder_message(msg.telephone, "assistant", reponse)
    await fournisseur.envoyer_message(msg.telephone, reponse)

    if action and action["type"] == "contacter_bailleur":
        await creer_reparation(msg.telephone, action["resume"], action["disponibilites"])

        msg_marie = (
            f"📋 *Nouvelle demande d'intervention*\n\n"
            f"*Problème :* {action['resume']}\n\n"
            f"*Disponibilités {TENANT_NAME} :* {action['disponibilites']}\n\n"
            f"Réponds *OUI* pour valider l'intervention, ou *NON* avec la raison."
        )
        ok = await fournisseur.envoyer_message(LANDLORD_PHONE, msg_marie)
        if ok:
            logger.info(f"Message envoyé à Marie ({LANDLORD_PHONE})")
        else:
            logger.error(f"Échec envoi à Marie ({LANDLORD_PHONE}) — voir logs Twilio")


async def handle_bailleur(msg):
    historique = await obtenir_historique(msg.telephone)
    repair = await obtenir_reparation_en_attente("WAITING_LANDLORD")

    if not repair:
        await fournisseur.envoyer_message(
            msg.telephone,
            f"Bonjour {LANDLORD_NAME} ! Pas de demande d'intervention en attente pour l'instant. 👍"
        )
        return

    reponse_marie, action = await generer_reponse_bailleur(msg.texte, historique, repair)

    await sauvegarder_message(msg.telephone, "user", msg.texte)
    await sauvegarder_message(msg.telephone, "assistant", reponse_marie)
    await fournisseur.envoyer_message(msg.telephone, reponse_marie)

    if action:
        if action["type"] == "approuver":
            await mettre_a_jour_statut(repair.id, "CONFIRMED")

            msg_thomas = (
                f"✅ *Bonne nouvelle !* {LANDLORD_NAME} a validé l'intervention.\n\n"
                f"Voici 3 créneaux disponibles :\n\n"
                + "\n".join(CRENEAUX)
                + "\n\nQuel créneau te convient ? (réponds *1*, *2* ou *3*)"
            )
            await fournisseur.envoyer_message(repair.tenant_phone, msg_thomas)
            logger.info(f"Réparation {repair.id} approuvée → Thomas notifié")

        elif action["type"] == "refuser":
            await mettre_a_jour_statut(repair.id, "REJECTED")

            msg_thomas = (
                f"ℹ️ {LANDLORD_NAME} a été contactée pour la panne signalée.\n\n"
                f"Elle gère la situation : *{action.get('raison', 'en cours de traitement')}*\n\n"
                "Je te tiens au courant dès qu'on a plus d'infos !"
            )
            await fournisseur.envoyer_message(repair.tenant_phone, msg_thomas)
            logger.info(f"Réparation {repair.id} refusée → Thomas notifié")
