# ===============================================================================
# VELMO’S MEMORY BRAIN
# Récupération épisodique
# ===============================================================================

"""Stratégie de rappel des souvenirs anciens (mémoire long terme épisodique).

Isolée derrière le protocole `EpisodicRetriever` : l'orchestrateur ne connaît que
`.recall(...)`. Le repli hors-ligne est le recouvrement lexical (`LexicalRetriever`) ;
une recherche sémantique (Chroma) pourra le remplacer sans toucher au manager, en
implémentant le même protocole. `user_id` figure dans la signature car
l'isolation par utilisateur est le contrat non négociable de tout backend réel.
"""

from __future__ import annotations

import re
from typing import Protocol

from .store import Turn

# Mots trop courants pour porter du sens : ignorés dans le recouvrement lexical.
_STOP = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "au", "aux",
    "en", "est", "sur", "mon", "ma", "mes", "ton", "ta", "tes", "je", "tu",
    "il", "elle", "que", "qui", "quel", "quelle", "etait", "vous", "pour",
    "avec", "dans", "par", "the", "of",
}


def _tokens(text: str) -> set[str]:
    """Mots signifiants d'un texte (minuscules, sans ponctuation ni mots vides)."""
    words = re.split(r"[^0-9a-zàâäéèêëïîôöùûüç]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


class EpisodicRetriever(Protocol):
    """Contrat de rappel épisodique : remonte les `k` souvenirs les plus proches."""

    def recall(
        self, user_id: str, message: str, candidates: list[Turn], k: int
    ) -> list[str]:
        ...


class LexicalRetriever:
    """Rappel par recouvrement lexical (repli hors-ligne, sans dépendance réseau).

    Score chaque souvenir candidat par le nombre de mots signifiants partagés avec
    le message courant, et remonte les `k` meilleurs. `user_id` est ignoré : les
    candidats sont déjà filtrés par utilisateur en amont par l'orchestrateur.
    """

    def recall(
        self, user_id: str, message: str, candidates: list[Turn], k: int
    ) -> list[str]:
        query = _tokens(message)
        if not query:
            return []
        scored: list[tuple[int, str]] = []
        for _role, content in candidates:
            overlap = len(query & _tokens(content))
            if overlap:
                scored.append((overlap, content))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:k]]
