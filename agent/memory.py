# agent/memory.py — Historique des conversations

from datetime import datetime
from sqlalchemy import String, Text, DateTime, select, Integer
from sqlalchemy.orm import Mapped, mapped_column

from agent.db import Base, engine, async_session


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telephone: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    contenu: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def initialiser_db():
    import agent.repairs   # noqa
    import agent.notes     # noqa
    import agent.dossiers  # noqa
    import agent.journal   # noqa
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def sauvegarder_message(telephone: str, role: str, contenu: str):
    async with async_session() as session:
        msg = Message(telephone=telephone, role=role, contenu=contenu, timestamp=datetime.utcnow())
        session.add(msg)
        await session.commit()


async def obtenir_historique(telephone: str, limite: int = 20) -> list[dict]:
    async with async_session() as session:
        query = (
            select(Message)
            .where(Message.telephone == telephone)
            .order_by(Message.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        messages = result.scalars().all()
        messages.reverse()
        return [{"role": m.role, "content": m.contenu} for m in messages]


async def effacer_historique(telephone: str):
    async with async_session() as session:
        query = select(Message).where(Message.telephone == telephone)
        result = await session.execute(query)
        for msg in result.scalars().all():
            await session.delete(msg)
        await session.commit()
