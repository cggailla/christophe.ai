# agent/providers/__init__.py — Factory de fournisseurs WhatsApp

import os
from agent.providers.base import FournisseurWhatsApp


def obtenir_fournisseur() -> FournisseurWhatsApp:
    fournisseur = os.getenv("WHATSAPP_PROVIDER", "twilio").lower()

    if fournisseur == "twilio":
        from agent.providers.twilio import FournisseurTwilio
        return FournisseurTwilio()
    elif fournisseur == "whapi":
        from agent.providers.whapi import FournisseurWhapi
        return FournisseurWhapi()
    elif fournisseur == "meta":
        from agent.providers.meta import FournisseurMeta
        return FournisseurMeta()
    else:
        raise ValueError(f"Fournisseur non supporté : {fournisseur}. Utilise : twilio, whapi, ou meta")
