# agent/dossiers.py — Dossiers avec milestones pour workflows multi-étapes

import json
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.db import Base, async_session


class Dossier(Base):
    __tablename__ = "dossiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    titre: Mapped[str] = mapped_column(String(200))
    milestones_json: Mapped[str] = mapped_column(Text)  # JSON list of {id, label, statut}
    statut: Mapped[str] = mapped_column(String(20), default="ACTIF")  # ACTIF | TERMINE | ANNULE
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_broadcast_hook = None  # async callable(event_type, data)


async def _broadcast_dossier(dossier_dict: dict):
    if _broadcast_hook:
        await _broadcast_hook("dossier_update", {"dossier": dossier_dict})


async def creer_dossier(titre: str, milestones: list[str]) -> int:
    """Crée un nouveau dossier, clôt le précédent actif. Retourne l'id du nouveau dossier."""
    async with async_session() as session:
        # Clôturer le dossier actif s'il existe
        q = select(Dossier).where(Dossier.statut == "ACTIF")
        result = await session.execute(q)
        ancien = result.scalar_one_or_none()
        if ancien:
            ancien.statut = "ANNULE"
            ancien.updated_at = datetime.utcnow()

        milestones_data = [
            {"id": f"m{i+1}", "label": label, "statut": "EN_ATTENTE"}
            for i, label in enumerate(milestones)
        ]
        dossier = Dossier(
            titre=titre,
            milestones_json=json.dumps(milestones_data, ensure_ascii=False),
            statut="ACTIF",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(dossier)
        await session.commit()
        await session.refresh(dossier)
        dossier_id = dossier.id
        dossier_data = {
            "id": dossier_id, "titre": titre,
            "milestones": milestones_data, "statut": "ACTIF",
        }
    await _broadcast_dossier(dossier_data)
    return dossier_id


async def mettre_a_jour_milestone(milestone_id: str, statut: str) -> str:
    """Met à jour le statut d'un milestone dans le dossier actif."""
    async with async_session() as session:
        q = select(Dossier).where(Dossier.statut == "ACTIF")
        result = await session.execute(q)
        dossier = result.scalar_one_or_none()
        if not dossier:
            return "Aucun dossier actif."

        milestones = json.loads(dossier.milestones_json)
        trouve = False
        for m in milestones:
            if m["id"] == milestone_id:
                m["statut"] = statut
                trouve = True
                break

        if not trouve:
            return f"Milestone '{milestone_id}' introuvable."

        # Si tous les milestones sont FAIT, clôturer le dossier
        if all(m["statut"] in ("FAIT", "IGNORE") for m in milestones):
            dossier.statut = "TERMINE"

        dossier.milestones_json = json.dumps(milestones, ensure_ascii=False)
        dossier.updated_at = datetime.utcnow()
        await session.commit()
        dossier_data = {
            "id": dossier.id, "titre": dossier.titre,
            "milestones": milestones, "statut": dossier.statut,
        }
    await _broadcast_dossier(dossier_data)
    return f"Milestone '{milestone_id}' → {statut}."


async def reviser_dossier(milestones_revises: list[dict], titre: str = None) -> str:
    """
    Révise complètement le plan du dossier actif.
    milestones_revises : liste de {label, statut} dans l'ordre voulu.
    Les IDs sont regénérés (m1, m2, …) pour refléter le nouvel ordre.
    """
    async with async_session() as session:
        q = select(Dossier).where(Dossier.statut == "ACTIF")
        result = await session.execute(q)
        dossier = result.scalar_one_or_none()
        if not dossier:
            return "Aucun dossier actif à réviser. Utilise creer_dossier d'abord."

        nouveaux = []
        for i, m in enumerate(milestones_revises):
            statut = m.get("statut", "EN_ATTENTE")
            if statut not in ("FAIT", "EN_COURS", "EN_ATTENTE", "IGNORE"):
                statut = "EN_ATTENTE"
            nouveaux.append({
                "id": f"m{i+1}",
                "label": m.get("label", ""),
                "statut": statut,
            })

        dossier.milestones_json = json.dumps(nouveaux, ensure_ascii=False)
        if titre:
            dossier.titre = titre
        dossier.updated_at = datetime.utcnow()
        await session.commit()
        dossier_data = {
            "id": dossier.id, "titre": dossier.titre,
            "milestones": nouveaux, "statut": dossier.statut,
        }

    await _broadcast_dossier(dossier_data)
    return f"Plan révisé — {len(nouveaux)} étapes."


async def lire_dossier_actif() -> dict | None:
    """Retourne le dossier actif ou None."""
    async with async_session() as session:
        q = select(Dossier).where(Dossier.statut == "ACTIF")
        result = await session.execute(q)
        dossier = result.scalar_one_or_none()
        if not dossier:
            return None
        return {
            "id": dossier.id,
            "titre": dossier.titre,
            "milestones": json.loads(dossier.milestones_json),
            "statut": dossier.statut,
        }


async def effacer_tous_dossiers():
    async with async_session() as session:
        result = await session.execute(select(Dossier))
        for d in result.scalars().all():
            await session.delete(d)
        await session.commit()


def formater_dossier(dossier: dict) -> str:
    """Formate un dossier pour injection dans le contexte de l'agent."""
    icones = {"FAIT": "✅", "EN_ATTENTE": "⏳", "IGNORE": "⏭️", "EN_COURS": "🔄"}
    lignes = [f"📋 **Dossier en cours : {dossier['titre']}**"]
    for m in dossier["milestones"]:
        icone = icones.get(m["statut"], "•")
        lignes.append(f"  {icone} [{m['id']}] {m['label']} — {m['statut']}")
    return "\n".join(lignes)
