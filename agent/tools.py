# agent/tools.py — Outils métier de Christophe

import os
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("christophe")


def charger_info_business() -> dict:
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml introuvable")
        return {}


def obtenir_horaire() -> dict:
    info = charger_info_business()
    return {
        "horaire": info.get("negocio", {}).get("horario", "24h/24, 7j/7"),
        "est_ouvert": True,
    }


def rechercher_dans_knowledge(requete: str) -> str:
    resultats = []
    knowledge_dir = "knowledge"

    if not os.path.exists(knowledge_dir):
        return "Pas de fichiers de connaissance disponibles."

    for racine, dossiers, fichiers in os.walk(knowledge_dir):
        # Ignorer node_modules et fichiers cachés
        dossiers[:] = [d for d in dossiers if d != "node_modules" and not d.startswith(".")]
        for fichier in fichiers:
            if fichier.startswith(".") or not fichier.endswith((".md", ".txt", ".json", ".csv")):
                continue
            chemin = os.path.join(racine, fichier)
            try:
                with open(chemin, "r", encoding="utf-8") as f:
                    contenu = f.read()
                    if requete.lower() in contenu.lower():
                        resultats.append(f"[{fichier}]: {contenu[:800]}")
            except (UnicodeDecodeError, IOError):
                continue

    if resultats:
        return "\n---\n".join(resultats[:3])  # max 3 résultats
    return "Pas d'information spécifique trouvée sur ce sujet."


# Créneaux simulés pour la coordination de réparations
CRENEAUX_SIMULÉS = [
    {"id": "slot-1", "réparateur": "Plomberie Bastille", "datetime": "Demain 9h-11h", "disponible": True},
    {"id": "slot-2", "réparateur": "ChauffeRapide Paris", "datetime": "Demain 14h-16h", "disponible": True},
    {"id": "slot-3", "réparateur": "ClimatPlus 75", "datetime": "Après-demain 10h-12h", "disponible": True},
]


def obtenir_creneaux(type_reparation: str) -> list[dict]:
    return CRENEAUX_SIMULÉS


def confirmer_rdv(slot_id: str) -> dict:
    for slot in CRENEAUX_SIMULÉS:
        if slot["id"] == slot_id:
            return {
                "success": True,
                "réparateur": slot["réparateur"],
                "datetime": slot["datetime"],
                "message": f"RDV confirmé avec {slot['réparateur']} — {slot['datetime']}",
            }
    return {"success": False, "message": "Créneau non trouvé."}
