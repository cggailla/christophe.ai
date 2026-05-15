# agent/providers/twilio.py — Adaptateur Twilio WhatsApp

import os
import hmac
import hashlib
import logging
import base64
import httpx
import mimetypes
from fastapi import Request
from agent.providers.base import FournisseurWhatsApp, MessageEntrant

logger = logging.getLogger("christophe")


class FournisseurTwilio(FournisseurWhatsApp):

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.numero = os.getenv("TWILIO_PHONE_NUMBER")

    def _signature_valide(self, url: str, params: dict, signature: str) -> bool:
        """
        Valide la signature Twilio (HMAC-SHA1 sur URL + params triés, puis base64).
        Doc : https://www.twilio.com/docs/usage/webhooks/webhooks-security
        """
        if not self.auth_token or not signature:
            return False
        data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
        mac = hmac.new(self.auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
        attendu = base64.b64encode(mac).decode("utf-8")
        return hmac.compare_digest(attendu, signature)

    async def parser_webhook(self, request: Request) -> list[MessageEntrant]:
        form = await request.form()

        # Validation de la signature Twilio : refuse tout webhook non signé.
        # En dev (TWILIO_AUTH_TOKEN vide) on bypasse pour ne pas bloquer les tests.
        if self.auth_token:
            signature = request.headers.get("X-Twilio-Signature", "")
            # Twilio signe avec l'URL publique (ngrok). FastAPI voit localhost → mismatch.
            # On reconstruit l'URL réelle depuis PUBLIC_URL ou les headers de forwarding.
            public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
            if public_url:
                url = f"{public_url}{request.url.path}"
            else:
                proto = request.headers.get("x-forwarded-proto", "http")
                host = request.headers.get("x-forwarded-host", str(request.url.netloc))
                url = f"{proto}://{host}{request.url.path}"
            params = {k: form.get(k, "") for k in form.keys()}
            if not self._signature_valide(url, params, signature):
                logger.warning(f"Signature Twilio invalide pour {url} — rejet")
                return []

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

    async def envoyer_document(self, telephone: str, filepath: str, legende: str = "") -> bool:
        """Envoie un PDF via Twilio WhatsApp en utilisant PUBLIC_URL pour héberger le fichier."""
        public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
        if not public_url:
            logger.warning("PUBLIC_URL non configurée — impossible d'envoyer le document via Twilio")
            return False
        filename = os.path.basename(filepath)
        media_url = f"{public_url}/documents/{filename}"
        if not all([self.account_sid, self.auth_token, self.numero]):
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        data = {
            "From": f"whatsapp:{self.numero}",
            "To": f"whatsapp:{telephone}",
            "Body": legende or "Document joint",
            "MediaUrl": media_url,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data=data, headers={"Authorization": f"Basic {auth}"})
            if r.status_code != 201:
                logger.error(f"Erreur Twilio document : {r.status_code} — {r.text}")
            return r.status_code == 201
