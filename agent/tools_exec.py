# agent/tools_exec.py — Implémentation et dispatch de tous les outils

import os
import logging
import httpx
from agent.notes import sauvegarder_note, lire_note
from agent.repairs import creer_reparation, mettre_a_jour_statut
from agent.dossiers import creer_dossier, mettre_a_jour_milestone, lire_dossier_actif, reviser_dossier

logger = logging.getLogger("christophe")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Hook optionnel pour broadcaster les tool calls (utilisé par l'interface locale)
_broadcast_hook = None  # async callable(event_type: str, data: dict)

CRENEAUX_SIMULÉS = [
    {"id": "slot-1", "prestataire": "Plomberie Bastille",  "creneau": "Lundi 9h-11h"},
    {"id": "slot-2", "prestataire": "ChauffeRapide Paris", "creneau": "Lundi 14h-16h"},
    {"id": "slot-3", "prestataire": "ClimatPlus 75",       "creneau": "Mardi 10h-12h"},
]

# ── Définitions des outils (schema JSON pour Claude) ─────────────────────────

TOOLS = [
    {
        "name": "recherche_web",
        "description": (
            "Recherche des informations sur internet en temps réel. "
            "Utilise pour : trouver des produits avec prix et liens, vérifier des informations légales, "
            "trouver des prestataires locaux, comparer des options, obtenir des actualités."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "La requête de recherche précise"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "contacter_partie",
        "description": (
            "Envoie un message WhatsApp à une partie (locataire ou bailleur). "
            "Utilise pour notifier l'autre partie, demander une validation, partager des options, "
            "ou faire un suivi. N'attends pas — contacte directement quand c'est nécessaire."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destinataire": {
                    "type": "string",
                    "enum": ["locataire", "bailleur"],
                    "description": "Qui contacter",
                },
                "message": {"type": "string", "description": "Le message à envoyer"},
            },
            "required": ["destinataire", "message"],
        },
    },
    {
        "name": "obtenir_creneaux",
        "description": "Obtient les créneaux disponibles pour une intervention ou une livraison.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type_intervention": {
                    "type": "string",
                    "description": "Type d'intervention (plomberie, électricité, livraison meuble, peinture, etc.)",
                },
                "zone": {"type": "string", "description": "Zone ou arrondissement"},
            },
            "required": ["type_intervention"],
        },
    },
    {
        "name": "confirmer_rdv",
        "description": "Confirme un rendez-vous avec un prestataire et enregistre l'intervention.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prestataire": {"type": "string"},
                "creneau": {"type": "string"},
                "type_intervention": {"type": "string"},
                "details": {"type": "string", "description": "Notes ou instructions pour le prestataire"},
            },
            "required": ["prestataire", "creneau", "type_intervention"],
        },
    },
    {
        "name": "sauvegarder_note",
        "description": (
            "Sauvegarde une information importante pour s'en souvenir entre les conversations. "
            "Utilise pour : décisions prises, préférences exprimées, informations clés, engagements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cle": {"type": "string", "description": "Identifiant court de la note (ex: 'budget_canape', 'preference_marie')"},
                "valeur": {"type": "string", "description": "Contenu à mémoriser"},
            },
            "required": ["cle", "valeur"],
        },
    },
    {
        "name": "lire_note",
        "description": "Lit une note sauvegardée précédemment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cle": {"type": "string", "description": "Identifiant de la note à lire"},
            },
            "required": ["cle"],
        },
    },
    {
        "name": "generer_et_envoyer_document",
        "description": (
            "Génère un document PDF officiel et l'envoie aux parties concernées. "
            "Utilise pour : quittance de loyer, récapitulatif de réparation, tout document à transmettre. "
            "Tu dois remplir toutes les données depuis ta connaissance du bien et du bail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type_document": {
                    "type": "string",
                    "enum": ["quittance_loyer"],
                    "description": "Type de document à générer",
                },
                "donnees": {
                    "type": "object",
                    "description": (
                        "Données pour remplir le document. Pour quittance_loyer : "
                        "tenant_name, landlord_name, property_address, periode (ex: 'juillet 2025'), "
                        "date_debut (ex: '01/07/2025'), date_fin (ex: '31/07/2025'), "
                        "loyer_hc (nombre), charges (nombre), total (nombre), date_emission."
                    ),
                },
                "destinataires": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["locataire", "bailleur"]},
                    "description": "Liste des parties qui reçoivent le document",
                },
                "message_accompagnement": {
                    "type": "string",
                    "description": "Message court qui accompagne l'envoi du document",
                },
            },
            "required": ["type_document", "donnees", "destinataires"],
        },
    },
    {
        "name": "creer_dossier",
        "description": (
            "Crée un dossier de suivi avec des milestones pour un workflow multi-étapes. "
            "Utilise dès qu'une demande implique plusieurs échanges entre parties (remplacement meuble, "
            "réparation, demande de travaux, etc.). "
            "Le dossier permet de toujours savoir où en est le processus. "
            "Exemples de milestones : ['Marie contactée pour le canapé', 'Marie choisit le modèle', "
            "'Thomas donne ses disponibilités', 'Livraison confirmée']."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "titre": {"type": "string", "description": "Titre court du dossier (ex: 'Remplacement canapé')"},
                "milestones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Liste ordonnée des étapes à franchir",
                },
            },
            "required": ["titre", "milestones"],
        },
    },
    {
        "name": "mettre_a_jour_milestone",
        "description": (
            "Met à jour le statut d'une étape dans le dossier actif. "
            "Appelle après chaque action complétée pour garder le dossier à jour. "
            "Statuts possibles : FAIT (étape complétée), EN_COURS (en train de faire), "
            "IGNORE (non applicable)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "milestone_id": {"type": "string", "description": "ID du milestone (ex: 'm1', 'm2', 'm3')"},
                "statut": {
                    "type": "string",
                    "enum": ["FAIT", "EN_COURS", "IGNORE"],
                    "description": "Nouveau statut",
                },
            },
            "required": ["milestone_id", "statut"],
        },
    },
    {
        "name": "reviser_dossier",
        "description": (
            "Révise le plan du dossier actif : ajoute des étapes manquantes, retire celles devenues "
            "obsolètes, réorganise l'ordre, change le titre si la portée évolue. "
            "Utilise dès que la situation change ou qu'une demande sort du plan initial. "
            "Tu fournis la liste COMPLÈTE des milestones (anciens conservés + nouveaux ajoutés) "
            "avec leur statut actuel. Les IDs sont regénérés automatiquement."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "milestones": {
                    "type": "array",
                    "description": "Liste complète et ordonnée des étapes du plan révisé",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "Description précise de l'étape"},
                            "statut": {
                                "type": "string",
                                "enum": ["FAIT", "EN_COURS", "EN_ATTENTE", "IGNORE"],
                                "description": "Statut actuel de cette étape",
                            },
                        },
                        "required": ["label", "statut"],
                    },
                },
                "titre": {
                    "type": "string",
                    "description": "Nouveau titre du dossier si sa portée a évolué (optionnel)",
                },
            },
            "required": ["milestones"],
        },
    },
    {
        "name": "lire_dossier_actif",
        "description": (
            "Lit le dossier actif pour savoir où en est le workflow en cours. "
            "Utilise quand tu as un doute sur l'étape actuelle ou quand quelqu'un demande "
            "'on en est où ?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "escalader",
        "description": (
            "Signale une situation qui dépasse le cadre de Christophe et nécessite une intervention humaine : "
            "conflit grave, situation légale complexe, urgence médicale ou sécurité, impayé persistant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "raison": {"type": "string", "description": "Description précise de la situation"},
                "urgence": {
                    "type": "string",
                    "enum": ["normale", "urgente", "critique"],
                    "description": "Niveau d'urgence",
                },
            },
            "required": ["raison", "urgence"],
        },
    },
]


# ── Exécution des outils ──────────────────────────────────────────────────────

async def executer_outil(nom: str, inputs: dict, ctx: dict) -> str:
    """
    Dispatch vers l'implémentation du tool.
    ctx contient : speaker, speaker_phone, autre_phone, autre_name, fournisseur
    """
    logger.info(f"Tool → {nom}({inputs})")

    if _broadcast_hook:
        await _broadcast_hook("tool_call", {"tool": nom, "input": inputs})

    result = await _dispatcher(nom, inputs, ctx)
    await _auto_log(nom, inputs, result, ctx)
    return result


async def _auto_log(nom: str, inputs: dict, result: str, ctx: dict):
    """Enregistre automatiquement les actions significatives dans le journal."""
    from agent.journal import enregistrer_action

    speaker_name = ctx.get("speaker_name", "?")
    tenant_name  = ctx.get("tenant_name", "le locataire")
    landlord_name = ctx.get("landlord_name", "le bailleur")

    if nom == "contacter_partie":
        dest = inputs.get("destinataire", "")
        dest_name = landlord_name if dest == "bailleur" else tenant_name
        msg_preview = inputs.get("message", "")[:80].replace("\n", " ")
        await enregistrer_action("message_envoyé",
            f'📨 Message envoyé à {dest_name} : "{msg_preview}{"…" if len(inputs.get("message",""))>80 else ""}"')

    elif nom == "generer_et_envoyer_document":
        donnees = inputs.get("donnees", {})
        periode = donnees.get("periode", "")
        type_doc = inputs.get("type_document", "document")
        dests = inputs.get("destinataires", [])
        dest_names = [landlord_name if d == "bailleur" else tenant_name for d in dests]
        await enregistrer_action("document",
            f"✅ {type_doc} {periode} généré et envoyé à : {', '.join(dest_names)}")

    elif nom == "confirmer_rdv":
        await enregistrer_action("rdv",
            f"📅 RDV confirmé : {inputs.get('prestataire')} — {inputs.get('creneau')} ({inputs.get('type_intervention')})")

    elif nom == "creer_dossier":
        await enregistrer_action("dossier",
            f"📋 Dossier ouvert : \"{inputs.get('titre')}\" ({len(inputs.get('milestones',[]))} étapes)")

    elif nom == "reviser_dossier":
        n = len(inputs.get("milestones", []))
        titre_part = f" — nouveau titre : \"{inputs['titre']}\"" if inputs.get("titre") else ""
        await enregistrer_action("dossier",
            f"🔧 Plan révisé : {n} étapes au total{titre_part}")

    elif nom == "mettre_a_jour_milestone":
        if inputs.get("statut") == "FAIT":
            await enregistrer_action("milestone",
                f"✅ Étape validée : {inputs.get('milestone_id')} dans le dossier actif")

    elif nom == "escalader":
        await enregistrer_action("escalade",
            f"🚨 Escalade [{inputs.get('urgence')}] : {inputs.get('raison','')[:80]}")


async def _dispatcher(nom: str, inputs: dict, ctx: dict) -> str:
    """Dispatch effectif vers l'implémentation du tool."""
    if nom == "recherche_web":
        return await _recherche_web(inputs["query"])

    elif nom == "contacter_partie":
        return await _contacter_partie(inputs["destinataire"], inputs["message"], ctx)

    elif nom == "obtenir_creneaux":
        return _obtenir_creneaux(inputs["type_intervention"], inputs.get("zone", "Paris"))

    elif nom == "confirmer_rdv":
        return await _confirmer_rdv(inputs, ctx)

    elif nom == "sauvegarder_note":
        await sauvegarder_note(inputs["cle"], inputs["valeur"])
        return f"Note '{inputs['cle']}' sauvegardée."

    elif nom == "lire_note":
        valeur = await lire_note(inputs["cle"])
        return valeur if valeur else f"Aucune note trouvée pour '{inputs['cle']}'."

    elif nom == "generer_et_envoyer_document":
        return await _generer_et_envoyer_document(inputs, ctx)

    elif nom == "creer_dossier":
        dossier_id = await creer_dossier(inputs["titre"], inputs["milestones"])
        milestones_str = "\n".join(f"  • [m{i+1}] {m}" for i, m in enumerate(inputs["milestones"]))
        return f"Dossier #{dossier_id} créé : '{inputs['titre']}'\nMilestones :\n{milestones_str}"

    elif nom == "mettre_a_jour_milestone":
        return await mettre_a_jour_milestone(inputs["milestone_id"], inputs["statut"])

    elif nom == "reviser_dossier":
        return await reviser_dossier(inputs["milestones"], inputs.get("titre"))

    elif nom == "lire_dossier_actif":
        from agent.dossiers import formater_dossier
        dossier = await lire_dossier_actif()
        if not dossier:
            return "Aucun dossier actif en ce moment."
        return formater_dossier(dossier)

    elif nom == "escalader":
        logger.warning(f"ESCALADE [{inputs['urgence']}] : {inputs['raison']}")
        return f"Escalade enregistrée (urgence: {inputs['urgence']}). Un humain va être notifié."

    return f"Tool '{nom}' non reconnu."


async def _recherche_web(query: str) -> str:
    if not TAVILY_API_KEY:
        return "Recherche web non configurée (TAVILY_API_KEY manquant)."
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                },
            )
            data = r.json()
            answer = data.get("answer", "")
            results = data.get("results", [])
            parts = []
            if answer:
                parts.append(f"Synthèse : {answer}")
            for res in results[:4]:
                parts.append(f"• {res['title']}\n  {res['content'][:300]}\n  {res['url']}")
            return "\n\n".join(parts) if parts else "Aucun résultat trouvé."
    except Exception as e:
        logger.error(f"Erreur Tavily : {e}")
        return f"Erreur lors de la recherche : {e}"


async def _contacter_partie(destinataire: str, message: str, ctx: dict) -> str:
    fournisseur = ctx.get("fournisseur")
    if not fournisseur:
        return "Fournisseur WhatsApp non disponible."

    if destinataire == "bailleur":
        phone = ctx.get("landlord_phone", "")
        nom = ctx.get("landlord_name", "le bailleur")
    else:
        phone = ctx.get("tenant_phone", "")
        nom = ctx.get("tenant_name", "le locataire")

    if not phone:
        return f"Numéro de {destinataire} non configuré."

    ok = await fournisseur.envoyer_message(phone, message)
    if ok:
        logger.info(f"Message envoyé à {nom} ({phone})")
        # Sauvegarder dans l'historique du destinataire pour que Claude garde le contexte
        from agent.memory import sauvegarder_message
        await sauvegarder_message(phone, "assistant", message)
        return f"Message envoyé à {nom} avec succès."
    else:
        return f"Échec de l'envoi à {nom} — vérifier Twilio."


def _obtenir_creneaux(type_intervention: str, zone: str) -> str:
    lignes = [f"Créneaux disponibles pour '{type_intervention}' ({zone}) :"]
    for i, s in enumerate(CRENEAUX_SIMULÉS, 1):
        lignes.append(f"{i}. {s['prestataire']} — {s['creneau']} (id: {s['id']})")
    return "\n".join(lignes)


async def _confirmer_rdv(inputs: dict, ctx: dict) -> str:
    prestataire = inputs["prestataire"]
    creneau = inputs["creneau"]
    type_int = inputs["type_intervention"]
    details = inputs.get("details", "")

    # Enregistrer en DB
    tenant_phone = ctx.get("tenant_phone", "")
    if tenant_phone:
        await creer_reparation(
            tenant_phone,
            f"{type_int} — {prestataire} — {details}",
            creneau,
        )
        await mettre_a_jour_statut(
            (await _get_last_repair_id(tenant_phone)), "CONFIRMED"
        )

    return (
        f"RDV confirmé : {prestataire} intervient le {creneau} "
        f"pour '{type_int}'. {f'Notes : {details}' if details else ''}"
    )


async def _generer_et_envoyer_document(inputs: dict, ctx: dict) -> str:
    from agent.documents import generer_quittance
    from agent.memory import sauvegarder_message

    type_doc  = inputs.get("type_document", "quittance_loyer")
    donnees   = inputs.get("donnees", {})
    destinataires = inputs.get("destinataires", ["locataire", "bailleur"])
    legende   = inputs.get("message_accompagnement", "Veuillez trouver ci-joint votre document.")
    fournisseur = ctx.get("fournisseur")

    # Génération
    if type_doc == "quittance_loyer":
        try:
            filepath = generer_quittance(donnees)
        except Exception as e:
            return f"Erreur lors de la génération du PDF : {e}"
    else:
        return f"Type de document '{type_doc}' non supporté."

    filename = os.path.basename(filepath)
    envois = []

    for dest in destinataires:
        if dest == "locataire":
            phone = ctx.get("tenant_phone", "")
            nom   = ctx.get("tenant_name", "le locataire")
        else:
            phone = ctx.get("landlord_phone", "")
            nom   = ctx.get("landlord_name", "le bailleur")

        if not phone:
            continue

        ok = await fournisseur.envoyer_document(phone, filepath, legende)
        # Sauvegarder dans l'historique du destinataire
        await sauvegarder_message(phone, "assistant", f"[Document joint : {filename}] {legende}")
        envois.append(f"{'✅' if ok else '⚠️'} {nom}")

    return f"Document '{filename}' généré et envoyé à : {', '.join(envois)}."


async def _get_last_repair_id(tenant_phone: str) -> str:
    from agent.repairs import obtenir_reparation_en_attente
    r = await obtenir_reparation_en_attente("WAITING_LANDLORD")
    return r.id if r else ""
