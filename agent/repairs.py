# agent/repairs.py — Gestion des demandes de réparation

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.db import Base, async_session


class Reparation(Base):
    __tablename__ = "reparations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_phone: Mapped[str] = mapped_column(String(50), index=True)
    resume: Mapped[str] = mapped_column(Text, default="")
    disponibilites: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="WAITING_LANDLORD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def creer_reparation(tenant_phone: str, resume: str, disponibilites: str) -> str:
    repair_id = str(uuid.uuid4())
    async with async_session() as session:
        repair = Reparation(
            id=repair_id,
            tenant_phone=tenant_phone,
            resume=resume,
            disponibilites=disponibilites,
            status="WAITING_LANDLORD",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(repair)
        await session.commit()
    return repair_id


async def obtenir_reparation_en_attente(status: str = "WAITING_LANDLORD") -> Reparation | None:
    async with async_session() as session:
        query = (
            select(Reparation)
            .where(Reparation.status == status)
            .order_by(Reparation.created_at.desc())
            .limit(1)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()


async def effacer_toutes_reparations():
    async with async_session() as session:
        query = select(Reparation)
        result = await session.execute(query)
        for r in result.scalars().all():
            await session.delete(r)
        await session.commit()


async def mettre_a_jour_statut(repair_id: str, nouveau_statut: str):
    async with async_session() as session:
        query = select(Reparation).where(Reparation.id == repair_id)
        result = await session.execute(query)
        repair = result.scalar_one_or_none()
        if repair:
            repair.status = nouveau_statut
            repair.updated_at = datetime.utcnow()
            await session.commit()
