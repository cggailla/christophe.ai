# agent/notes.py — Mémoire persistante clé/valeur pour l'agent

from datetime import datetime
from sqlalchemy import String, Text, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.db import Base, async_session


class Note(Base):
    __tablename__ = "notes"

    cle: Mapped[str] = mapped_column(String(100), primary_key=True)
    valeur: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def sauvegarder_note(cle: str, valeur: str):
    async with async_session() as session:
        existing = await session.get(Note, cle)
        if existing:
            existing.valeur = valeur
            existing.updated_at = datetime.utcnow()
        else:
            session.add(Note(cle=cle, valeur=valeur, updated_at=datetime.utcnow()))
        await session.commit()


async def lire_note(cle: str) -> str | None:
    async with async_session() as session:
        note = await session.get(Note, cle)
        return note.valeur if note else None


async def effacer_toutes_notes():
    async with async_session() as session:
        result = await session.execute(select(Note))
        for note in result.scalars().all():
            await session.delete(note)
        await session.commit()
