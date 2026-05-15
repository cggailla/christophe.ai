# agent/dossiers.py — Dossiers avec milestones pour workflows multi-étapes
# Plusieurs dossiers peuvent être ACTIF en parallèle (un par workflow distinct).

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


def _serialize(dossier: "Dossier", milestones: list[dict]) -> dict:
    return {
        "id": dossier.id,
        "titre": dossier.titre,
        "milestones": milestones,
        "statut": dossier.statut,
    }


async def creer_dossier(titre: str, milestones: list[str]) -> int:
    """
    Crée un nouveau dossier ACTIF.
    Les autres dossiers ACTIF restent ouverts — Christophe peut suivre plusieurs
    workflows en parallèle (réparation + quittance + question simultanément).
    """
    async with async_session() as session:
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
        data = _serialize(dossier, milestones_data)
    await _broadcast_dossier(data)
    return dossier.id


async def _resoudre_dossier(session, dossier_id: int | None) -> "Dossier | None":
    """Retourne le dossier ciblé : l'id fourni, ou le plus récent ACTIF si absent."""
    if dossier_id is not None:
        q = select(Dossier).where(Dossier.id == dossier_id, Dossier.statut == "ACTIF")
    else:
        q = select(Dossier).where(Dossier.statut == "ACTIF").order_by(Dossier.updated_at.desc()).limit(1)
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def mettre_a_jour_milestone(milestone_id: str, statut: str, dossier_id: int | None = None) -> str:
    """
    Met à jour le statut d'un milestone.
    Si plusieurs dossiers sont actifs, précise dossier_id pour cibler le bon.
    """
    async with async_session() as session:
        dossier = await _resoudre_dossier(session, dossier_id)
        if not dossier:
            return "Aucun dossier actif correspondant."

        milestones = json.loads(dossier.milestones_json)
        trouve = next((m for m in milestones if m["id"] == milestone_id), None)
        if not trouve:
            return f"Milestone '{milestone_id}' introuvable dans le dossier #{dossier.id}."

        trouve["statut"] = statut
        if all(m["statut"] in ("FAIT", "IGNORE") for m in milestones):
            dossier.statut = "TERMINE"

        dossier.milestones_json = json.dumps(milestones, ensure_ascii=False)
        dossier.updated_at = datetime.utcnow()
        await session.commit()
        data = _serialize(dossier, milestones)

    await _broadcast_dossier(data)
    return f"Milestone '{milestone_id}' du dossier #{data['id']} → {statut}."


async def reviser_dossier(milestones_revises: list[dict], dossier_id: int | None = None, titre: str = None) -> str:
    """
    Révise le plan d'un dossier (ajoute/retire/réordonne).
    Précise dossier_id si plusieurs dossiers sont actifs.
    """
    async with async_session() as session:
        dossier = await _resoudre_dossier(session, dossier_id)
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
        data = _serialize(dossier, nouveaux)

    await _broadcast_dossier(data)
    return f"Dossier #{data['id']} révisé — {len(nouveaux)} étapes."


async def lire_dossiers_actifs() -> list[dict]:
    """Retourne tous les dossiers ACTIF, du plus récemment mis à jour au plus ancien."""
    async with async_session() as session:
        q = select(Dossier).where(Dossier.statut == "ACTIF").order_by(Dossier.updated_at.desc())
        result = await session.execute(q)
        return [
            {
                "id": d.id,
                "titre": d.titre,
                "milestones": json.loads(d.milestones_json),
                "statut": d.statut,
            }
            for d in result.scalars().all()
        ]


# Conservé pour compat : renvoie le plus récent.
async def lire_dossier_actif() -> dict | None:
    actifs = await lire_dossiers_actifs()
    return actifs[0] if actifs else None


async def effacer_tous_dossiers():
    async with async_session() as session:
        result = await session.execute(select(Dossier))
        for d in result.scalars().all():
            await session.delete(d)
        await session.commit()


def formater_dossier(dossier: dict) -> str:
    icones = {"FAIT": "✅", "EN_ATTENTE": "⏳", "IGNORE": "⏭️", "EN_COURS": "🔄"}
    lignes = [f"📋 **Dossier #{dossier['id']} : {dossier['titre']}**"]
    for m in dossier["milestones"]:
        icone = icones.get(m["statut"], "•")
        lignes.append(f"  {icone} [{m['id']}] {m['label']} — {m['statut']}")
    return "\n".join(lignes)


def formater_dossiers_actifs(dossiers: list[dict]) -> str:
    if not dossiers:
        return "Aucun dossier actif."
    return "\n\n".join(formater_dossier(d) for d in dossiers)
