# agent/brain.py — Agent loop unifié avec tool use complet

import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from agent.tools_exec import TOOLS, executer_outil
from agent.dossiers import lire_dossier_actif, formater_dossier, creer_dossier
from agent.journal import lire_journal, formater_journal, enregistrer_action

load_dotenv(override=True)
logger = logging.getLogger("christophe")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MAX_ITERATIONS = 10  # sécurité anti-boucle infinie


def charger_system_prompt() -> str:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            return cfg.get("system_prompt", "Tu es Christophe, agent de gestion immobilière.")
    except FileNotFoundError:
        return "Tu es Christophe, agent de gestion immobilière."


async def agent_loop(message: str, historique: list[dict], ctx: dict) -> str:
    """
    Boucle agentique complète.
    Claude tourne jusqu'à avoir une réponse finale, en appelant tous les tools nécessaires.

    ctx = {
        speaker: "locataire" | "bailleur",
        speaker_name, speaker_phone,
        tenant_name, tenant_phone,
        landlord_name, landlord_phone,
        fournisseur: instance Twilio
    }
    """
    if not message or len(message.strip()) < 2:
        return "Désolé, je n'ai pas bien compris. Tu peux reformuler ?"

    system_prompt = charger_system_prompt()

    # Injecter qui parle dans le contexte
    system_prompt += (
        f"\n\n## Conversation en cours\n"
        f"Tu parles avec : **{ctx.get('speaker_name')} ({ctx.get('speaker')})**\n"
        f"Locataire : {ctx.get('tenant_name')} — {ctx.get('tenant_phone')}\n"
        f"Bailleur : {ctx.get('landlord_name')} — {ctx.get('landlord_phone')}\n"
    )

    # Enregistrer le message entrant dans le journal (fait par le système, pas par Claude)
    speaker_name = ctx.get("speaker_name", "?")
    msg_preview = message[:100].replace("\n", " ")
    await enregistrer_action("message_recu",
        f'💬 {speaker_name} : "{msg_preview}{"…" if len(message) > 100 else ""}"')

    # Injecter le journal des faits accomplis
    journal = await lire_journal(limite=20)
    journal_txt = formater_journal(journal)
    system_prompt += (
        f"\n\n## Journal des actions — LIRE AVANT D'AGIR\n"
        f"Ce registre est écrit automatiquement par le système. Il est fiable à 100%.\n"
        f"Ne refais jamais une action déjà marquée ✅ dans ce journal.\n"
        f"Si une quittance, un document ou un message est déjà dans le journal → ne pas le refaire.\n\n"
        f"{journal_txt}\n"
    )

    # Injecter le dossier actif s'il existe
    dossier = await lire_dossier_actif()
    if dossier:
        system_prompt += f"\n## Dossier actif — à réviser AVANT toute action\n{formater_dossier(dossier)}\n"
        system_prompt += (
            "\nRevue obligatoire avant d'agir :\n"
            "- Le message qui vient d'arriver est-il couvert par ces étapes ?\n"
            "- Une étape doit-elle être ajoutée, modifiée ou retirée ?\n"
            "- Si oui → appelle reviser_dossier AVANT toute autre action.\n"
            "- Sinon → appelle mettre_a_jour_milestone au fur et à mesure que tu avances.\n"
        )
    else:
        system_prompt += (
            "\n## Pas de dossier actif\n"
            "Ta toute première action doit être creer_dossier avec un plan complet adapté à la demande.\n"
        )

    messages = list(historique) + [{"role": "user", "content": message}]

    # Garde-fou : si Claude ne crée pas de dossier dans sa première itération,
    # on détecte ça après et on le force via injection dans les résultats tools.
    dossier_cree_cette_session = False

    for iteration in range(MAX_ITERATIONS):
        try:
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system_prompt,
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:
            logger.error(f"Erreur Claude API (itération {iteration}) : {e}")
            return "Oups, j'ai un petit problème technique. Réessaie dans quelques minutes !"

        # Plus de tools à appeler → réponse finale
        if response.stop_reason != "tool_use":
            texte = next((b.text for b in response.content if hasattr(b, "text")), "")
            logger.info(f"Agent terminé en {iteration + 1} itération(s)")
            return texte

        # Exécuter tous les tools appelés dans cette itération
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "creer_dossier":
                dossier_cree_cette_session = True
            logger.info(f"[iter {iteration + 1}] Tool call : {block.name}")
            result = await executer_outil(block.name, block.input, ctx)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        # Garde-fou : si première itération sans creer_dossier → créer un dossier générique
        if iteration == 0 and not dossier_cree_cette_session:
            dossier_existant = await lire_dossier_actif()
            if not dossier_existant:
                msg_court = message[:60].replace("\n", " ")
                await creer_dossier(
                    f"Demande — {msg_court}{'…' if len(message) > 60 else ''}",
                    ["Demande reçue et analysée", "Actions en cours", "Réponse fournie"]
                )
                logger.info("Garde-fou : dossier générique créé car Claude a omis creer_dossier")

        # Ajouter le tour assistant + les résultats tools
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    logger.warning("MAX_ITERATIONS atteint — agent stoppé")
    return "J'ai traité ta demande mais la réponse a pris trop d'étapes. Peux-tu reformuler ?"
