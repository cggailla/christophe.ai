# tests/test_local.py — Simulateur de chat en terminal pour Christophe

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generer_reponse
from agent.memory import initialiser_db, sauvegarder_message, obtenir_historique, effacer_historique

TELEPHONE_TEST = "test-local-001"


async def main():
    await initialiser_db()

    print()
    print("=" * 55)
    print("   Christophe.AI — Test Local")
    print("=" * 55)
    print()
    print("  Écris comme si tu étais Thomas (le locataire).")
    print("  Commandes spéciales :")
    print("    'effacer'  — réinitialise la conversation")
    print("    'quitter'  — termine le test")
    print()
    print("-" * 55)
    print()

    while True:
        try:
            message = input("Toi : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest terminé.")
            break

        if not message:
            continue

        if message.lower() == "quitter":
            print("\nTest terminé.")
            break

        if message.lower() == "effacer":
            await effacer_historique(TELEPHONE_TEST)
            print("[Historique effacé]\n")
            continue

        historique = await obtenir_historique(TELEPHONE_TEST)

        print("\nChristophe : ", end="", flush=True)
        reponse = await generer_reponse(message, historique)
        print(reponse)
        print()

        await sauvegarder_message(TELEPHONE_TEST, "user", message)
        await sauvegarder_message(TELEPHONE_TEST, "assistant", reponse)


if __name__ == "__main__":
    asyncio.run(main())
