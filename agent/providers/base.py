# agent/providers/base.py — Classe de base pour les fournisseurs WhatsApp

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MessageEntrant:
    telephone: str
    texte: str
    message_id: str
    est_propre: bool  # True si envoyé par l'agent lui-même (à ignorer)


class FournisseurWhatsApp(ABC):

    @abstractmethod
    async def parser_webhook(self, request: Request) -> list[MessageEntrant]:
        ...

    @abstractmethod
    async def envoyer_message(self, telephone: str, message: str) -> bool:
        ...

    async def valider_webhook(self, request: Request):
        return None
