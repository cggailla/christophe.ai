# agent/journal.py — Journal des faits accomplis, auto-alimenté par le système

from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.db import Base, async_session


class ActionJournal(Base):
    __tablename__ = "journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    type_action: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)


_broadcast_hook = None  # async callable(event_type, data)


async def enregistrer_action(type_action: str, description: str):
    async with async_session() as session:
        session.add(ActionJournal(
            timestamp=datetime.utcnow(),
            type_action=type_action,
            description=description,
        ))
        await session.commit()
    if _broadcast_hook:
        await _broadcast_hook("journal_entry", {
            "type": type_action,
            "desc": description,
            "time": datetime.utcnow().strftime("%H:%M"),
        })


async def lire_journal(limite: int = 20) -> list[dict]:
    async with async_session() as session:
        q = select(ActionJournal).order_by(ActionJournal.timestamp.desc()).limit(limite)
        result = await session.execute(q)
        entries = list(reversed(result.scalars().all()))
        return [
            {"timestamp": e.timestamp, "type": e.type_action, "desc": e.description}
            for e in entries
        ]


async def effacer_journal():
    async with async_session() as session:
        result = await session.execute(select(ActionJournal))
        for e in result.scalars().all():
            await session.delete(e)
        await session.commit()


def formater_journal(entries: list[dict]) -> str:
    if not entries:
        return "(aucune action encore)"
    lines = []
    for e in entries:
        t = e["timestamp"].strftime("%H:%M")
        lines.append(f"[{t}] {e['desc']}")
    return "\n".join(lines)
