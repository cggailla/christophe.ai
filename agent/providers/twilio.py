# agent/providers/twilio.py — Adaptateur Twilio WhatsApp

import os
import logging
import base64
import httpx
from fastapi import Request
from agent.providers.base import FournisseurWhatsApp, MessageEntrant

logger = logging.getLogger("christophe")


class FournisseurTwilio(FournisseurWhatsApp):

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.numero = os.getenv("TWILIO_PHONE_NUMBER")

    async def parser_webhook(self, request: Request) -> list[MessageEntrant]:
        form = await request.form()
        texte = form.get("Body", "").strip()
        expediteur = form.get("From", "")
        destinataire = form.get("To", "")
        message_id = form.get("MessageSid", "")

        # Ignorer les callbacks de statut (pas de Body)
        if not texte:
            return []

        # Ignorer les messages envoyés par le bot lui-même
        notre_numero = f"whatsapp:{self.numero}"
        if expediteur == notre_numero:
            return []

        telephone = expediteur.replace("whatsapp:", "")
        return [MessageEntrant(
            telephone=telephone,
            texte=texte,
            message_id=message_id,
            est_propre=False,
        )]

    async def envoyer_message(self, telephone: str, message: str) -> bool:
        if not all([self.account_sid, self.auth_token, self.numero]):
            logger.warning("Variables Twilio non configurées — message non envoyé")
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        data = {
            "From": f"whatsapp:{self.numero}",
            "To": f"whatsapp:{telephone}",
            "Body": message,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data=data, headers=headers)
            if r.status_code != 201:
                logger.error(f"Erreur Twilio : {r.status_code} — {r.text}")
            return r.status_code == 201
