#!/usr/bin/env python3
# tests/chat_local.py — Simulateur deux parties sans Twilio

"""
Usage :
  python tests/chat_local.py

Commandes :
  /thomas   → tu parles en tant que Thomas (locataire)
  /marie    → tu parles en tant que Marie (bailleur)
  clean     → reset complet (historique, notes, dossiers, réparations)
  quit      → quitter

Les messages envoyés à l'autre partie via contacter_partie s'affichent
directement dans le terminal avec un indicateur visuel.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from agent.brain import agent_loop
from agent.memory import initialiser_db, sauvegarder_message, obtenir_historique, effacer_historique
from agent.repairs import effacer_toutes_reparations
from agent.notes import effacer_toutes_notes
from agent.dossiers import effacer_tous_dossiers


# ── Mock fournisseur — remplace Twilio ───────────────────────────────────────

class MockFournisseur:
    """Capture les messages sortants et les affiche dans le terminal."""

    def __init__(self, tenant_phone: str, landlord_phone: str,
                 tenant_name: str, landlord_name: str):
        self.tenant_phone = tenant_phone
        self.landlord_phone = landlord_phone
        self.tenant_name = tenant_name
        self.landlord_name = landlord_name

    async def envoyer_message(self, phone: str, message: str) -> bool:
        if not message or not message.strip():
            print("  ⚠️  [message vide — non envoyé]")
            return False
        if phone == self.tenant_phone:
            nom = self.tenant_name
            couleur = "\033[94m"   # bleu
        elif phone == self.landlord_phone:
            nom = self.landlord_name
            couleur = "\033[92m"   # vert
        else:
            nom = phone
            couleur = "\033[93m"
        reset = "\033[0m"
        print(f"\n  {couleur}📤 → {nom} reçoit :{reset}")
        for ligne in message.split("\n"):
            print(f"     {ligne}")
        print()
        return True


# ── Chargement config ─────────────────────────────────────────────────────────

def charger_people() -> dict:
    try:
        with open("config/people.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def build_ctx(speaker: str, speaker_phone: str, people: dict, fournisseur) -> dict:
    landlord = people.get("landlord", {})
    tenant   = people.get("tenant", {})
    return {
        "speaker":        speaker,
        "speaker_name":   landlord["name"] if speaker == "bailleur" else tenant["name"],
        "speaker_phone":  speaker_phone,
        "tenant_name":    tenant.get("name", "Thomas"),
        "tenant_phone":   tenant.get("phone", ""),
        "landlord_name":  landlord.get("name", "Marie"),
        "landlord_phone": landlord.get("phone", ""),
        "fournisseur":    fournisseur,
    }


# ── Reset complet ─────────────────────────────────────────────────────────────

async def hard_reset(people: dict):
    tenant_phone   = people.get("tenant", {}).get("phone", "")
    landlord_phone = people.get("landlord", {}).get("phone", "")
    await effacer_historique(tenant_phone)
    await effacer_historique(landlord_phone)
    await effacer_toutes_reparations()
    await effacer_toutes_notes()
    await effacer_tous_dossiers()
    print("  🧹 Reset complet — historique, notes, dossiers, réparations effacés.\n")


# ── Boucle principale ─────────────────────────────────────────────────────────

async def main():
    await initialiser_db()

    people = charger_people()
    tenant   = people.get("tenant",   {"name": "Thomas", "phone": "local-thomas"})
    landlord = people.get("landlord", {"name": "Marie",  "phone": "local-marie"})

    fournisseur = MockFournisseur(
        tenant_phone=tenant["phone"],
        landlord_phone=landlord["phone"],
        tenant_name=tenant["name"],
        landlord_name=landlord["name"],
    )

    # Partie active par défaut : Thomas
    speaker        = "locataire"
    speaker_phone  = tenant["phone"]
    speaker_name   = tenant["name"]

    print()
    print("=" * 60)
    print("   Christophe.AI — Simulateur local (sans Twilio)")
    print("=" * 60)
    print(f"  Parle en tant que : \033[94m{speaker_name} (locataire)\033[0m")
    print()
    print("  /thomas → changer vers Thomas")
    print("  /marie  → changer vers Marie")
    print("  clean   → reset complet")
    print("  quit    → quitter")
    print("-" * 60)
    print()

    while True:
        # Prompt coloré selon la partie active
        if speaker == "locataire":
            prompt_label = f"\033[94m{speaker_name}\033[0m"
        else:
            prompt_label = f"\033[92m{speaker_name}\033[0m"

        try:
            texte = input(f"{prompt_label}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nAu revoir !")
            break

        if not texte:
            continue

        # Commandes de navigation
        if texte.lower() == "quit":
            print("Au revoir !")
            break

        if texte.lower() == "/thomas":
            speaker       = "locataire"
            speaker_phone = tenant["phone"]
            speaker_name  = tenant["name"]
            print(f"  → Maintenant tu parles en tant que \033[94m{speaker_name} (locataire)\033[0m\n")
            continue

        if texte.lower() == "/marie":
            speaker       = "bailleur"
            speaker_phone = landlord["phone"]
            speaker_name  = landlord["name"]
            print(f"  → Maintenant tu parles en tant que \033[92m{speaker_name} (bailleur)\033[0m\n")
            continue

        if texte.lower() == "clean":
            await hard_reset(people)
            continue

        # Construire le contexte et lancer l'agent
        ctx = build_ctx(speaker, speaker_phone, people, fournisseur)
        historique = await obtenir_historique(speaker_phone)

        print(f"\033[90m  [Christophe réfléchit...]\033[0m")
        reponse = await agent_loop(texte, historique, ctx)

        await sauvegarder_message(speaker_phone, "user", texte)
        await sauvegarder_message(speaker_phone, "assistant", reponse)

        print(f"\n\033[97mChristophe → {speaker_name} :\033[0m")
        for ligne in reponse.split("\n"):
            print(f"  {ligne}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
