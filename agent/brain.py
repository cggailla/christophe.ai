# agent/brain.py — Cerveau de Christophe : deux contextes (locataire / bailleur)

import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv(override=True)
logger = logging.getLogger("christophe")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Outils disponibles ────────────────────────────────────────────────────────

OUTIL_CONTACTER_BAILLEUR = {
    "name": "contacter_bailleur",
    "description": (
        "Envoie un message à la bailleuse Marie pour lui signaler le problème du locataire "
        "et demander sa validation pour une intervention. "
        "À utiliser UNIQUEMENT quand tu as collecté TOUTES les infos nécessaires : "
        "nature du problème, vérifications faites, et disponibilités du locataire."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "resume": {
                "type": "string",
                "description": "Résumé complet et clair du problème à envoyer à Marie."
            },
            "disponibilites": {
                "type": "string",
                "description": "Disponibilités précises du locataire pour recevoir un technicien."
            },
        },
        "required": ["resume", "disponibilites"],
    },
}

OUTIL_APPROUVER = {
    "name": "approuver_intervention",
    "description": "Marie approuve l'intervention. Christophe va informer Thomas et lui proposer des créneaux.",
    "input_schema": {
        "type": "object",
        "properties": {
            "commentaire": {
                "type": "string",
                "description": "Commentaire optionnel de Marie (préférences réparateur, remarques, etc.)"
            }
        },
        "required": [],
    },
}

OUTIL_REFUSER = {
    "name": "refuser_intervention",
    "description": "Marie refuse ou veut gérer elle-même l'intervention.",
    "input_schema": {
        "type": "object",
        "properties": {
            "raison": {
                "type": "string",
                "description": "Raison du refus ou explication de comment elle va gérer."
            }
        },
        "required": ["raison"],
    },
}

# ── Chargement des prompts ────────────────────────────────────────────────────

def charger_config() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml introuvable")
        return {}


def charger_system_prompt() -> str:
    return charger_config().get("system_prompt", "Tu es Christophe, assistant de gestion immobilière.")


def charger_landlord_system_prompt(repair=None) -> str:
    base = charger_config().get("landlord_system_prompt", "Tu es Christophe, tu parles avec la bailleuse Marie.")
    if repair:
        base += f"\n\n## Demande en attente de validation\n- Résumé : {repair.resume}\n- Disponibilités locataire : {repair.disponibilites}\n- ID : {repair.id}"
    return base


def message_erreur() -> str:
    return charger_config().get("error_message", "Oups, j'ai un petit problème technique. Réessaie dans quelques minutes !")


def message_fallback() -> str:
    return charger_config().get("fallback_message", "Désolé, je n'ai pas bien compris. Tu peux reformuler ?")


# ── Brain locataire ───────────────────────────────────────────────────────────

async def generer_reponse_tenant(
    message: str,
    historique: list[dict],
    repair_en_attente=None,
) -> tuple[str, dict | None]:
    """
    Retourne (texte_réponse, action_ou_None).
    action = {"type": "contacter_bailleur", "resume": ..., "disponibilites": ...}
    """
    if not message or len(message.strip()) < 2:
        return message_fallback(), None

    system_prompt = charger_system_prompt()

    # Inject repair context so Claude doesn't contact Marie twice
    if repair_en_attente:
        system_prompt += (
            f"\n\n⚠️ CONTEXTE : Tu as déjà envoyé une demande à Marie concernant : "
            f'"{repair_en_attente.resume}". Statut : EN ATTENTE DE RÉPONSE. '
            "Ne contacte pas Marie à nouveau pour ce problème. Informe Thomas que sa demande est en cours."
        )

    messages = list(historique) + [{"role": "user", "content": message}]

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=[OUTIL_CONTACTER_BAILLEUR],
        )

        if response.stop_reason == "tool_use":
            tool_block = next(b for b in response.content if b.type == "tool_use")
            action = {
                "type": "contacter_bailleur",
                "resume": tool_block.input["resume"],
                "disponibilites": tool_block.input["disponibilites"],
            }

            # Continuer avec le résultat de l'outil pour que Claude réponde à Thomas
            messages_cont = messages + [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": "Message envoyé à Marie avec succès. Elle a été notifiée.",
                        }
                    ],
                },
            ]

            final = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=messages_cont,
                tools=[OUTIL_CONTACTER_BAILLEUR],
            )

            texte_final = next((b.text for b in final.content if hasattr(b, "text")), message_erreur())
            logger.info(f"Tenant brain → action contacter_bailleur")
            return texte_final, action

        texte = next((b.text for b in response.content if hasattr(b, "text")), message_fallback())
        return texte, None

    except Exception as e:
        logger.error(f"Erreur Claude API (tenant) : {e}")
        return message_erreur(), None


# ── Brain bailleur ────────────────────────────────────────────────────────────

async def generer_reponse_bailleur(
    message: str,
    historique: list[dict],
    repair=None,
) -> tuple[str, dict | None]:
    """
    Retourne (texte_réponse_pour_Marie, action_ou_None).
    action = {"type": "approuver"|"refuser", ...}
    """
    if not message or len(message.strip()) < 2:
        return message_fallback(), None

    system_prompt = charger_landlord_system_prompt(repair)
    messages = list(historique) + [{"role": "user", "content": message}]

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=[OUTIL_APPROUVER, OUTIL_REFUSER],
        )

        if response.stop_reason == "tool_use":
            tool_block = next(b for b in response.content if b.type == "tool_use")

            if tool_block.name == "approuver_intervention":
                action = {"type": "approuver", "commentaire": tool_block.input.get("commentaire", "")}
            else:
                action = {"type": "refuser", "raison": tool_block.input.get("raison", "")}

            messages_cont = messages + [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": "Action exécutée. Thomas a été informé.",
                        }
                    ],
                },
            ]

            final = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=messages_cont,
                tools=[OUTIL_APPROUVER, OUTIL_REFUSER],
            )

            texte_final = next((b.text for b in final.content if hasattr(b, "text")), message_erreur())
            logger.info(f"Landlord brain → action {action['type']}")
            return texte_final, action

        texte = next((b.text for b in response.content if hasattr(b, "text")), message_fallback())
        return texte, None

    except Exception as e:
        logger.error(f"Erreur Claude API (bailleur) : {e}")
        return message_erreur(), None
