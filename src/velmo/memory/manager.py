# ===============================================================================
# VELMO’S MEMORY BRAIN
# The orchestrator
# ===============================================================================

"""Orchestration de la mémoire : reconstitue le contexte pertinent (faits +
souvenirs) et le tient au budget de tokens, en s'appuyant sur `MemoryStore`.

Étage court terme = les derniers tours ; étage long terme épisodique = les tours
plus anciens, remontés par recouvrement lexical avec le message courant. Les
faits durables (palier 2) complètent le contexte. La récupération lexicale est le
repli hors-ligne ; une recherche sémantique (Chroma) pourra la remplacer derrière
la même interface `MemoryStore`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .store import MemoryStore, Turn

# Nombre de tours récents gardés tels quels (mémoire court terme).
_RECENT = 10
# Nombre de souvenirs anciens remontés par pertinence (mémoire épisodique).
_EPISODIC_K = 3

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


def _estimate_tokens(text: str) -> int:
    """Estimation grossière du nombre de tokens (~4 caractères par token)."""
    return max(1, len(text) // 4)


@dataclass
class MemoryContext:
    """Contexte mémoire restitué pour une requête utilisateur."""

    history: list[Turn] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    episodic: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Sérialise le contexte en texte (injectable dans un prompt)."""
        parts: list[str] = []
        for role, content in self.history:
            parts.append(f"{role}: {content}")
        for key, value in self.facts.items():
            parts.append(f"fact:{key}={value}")
        parts.extend(self.episodic)
        return "\n".join(parts)


class MemoryManager:
    """Orchestre la mémoire court terme et long terme, isolée par utilisateur."""

    def __init__(self, *, token_budget: int = 2000, store: MemoryStore | None = None) -> None:
        self.token_budget = token_budget
        self._store = store or MemoryStore()

    def read(self, user_id: str, message: str) -> MemoryContext:
        """Reconstitue le contexte mémoire pertinent pour `message`."""
        facts = self._store.facts(user_id)
        episodes = self._store.episodes(user_id)

        recent = episodes[-_RECENT:]
        older = episodes[:-_RECENT] if len(episodes) > _RECENT else []
        episodic = self._retrieve(message, older)

        ctx = MemoryContext(history=recent, facts=facts, episodic=episodic)
        self._fit_budget(ctx)
        return ctx

    def write(self, user_id: str, user_message: str, assistant_message: str) -> None:
        """Met à jour la mémoire à partir d'un échange."""
        self._store.add_episode(user_id, "user", user_message)
        self._store.add_episode(user_id, "assistant", assistant_message)

    def remember_fact(self, user_id: str, key: str, value: str) -> None:
        """Persiste un fait durable sur l'utilisateur."""
        self._store.upsert_fact(user_id, key, value)

    def forget(self, user_id: str, target: str) -> int:
        """Supprime les souvenirs correspondant à `target`. Renvoie le nombre supprimé."""
        return self._store.forget(user_id, target)

    def inspect(self, user_id: str) -> dict:
        """Renvoie l'état mémoire d'un utilisateur (faits + souvenirs épisodiques)."""
        return {
            "facts": self._store.facts(user_id),
            "episodic": [content for _, content in self._store.episodes(user_id)],
        }

    # --- interne -------------------------------------------------------------

    def _retrieve(self, message: str, candidates: list[Turn]) -> list[str]:
        """Remonte les `_EPISODIC_K` souvenirs anciens les plus proches du message."""
        query = _tokens(message)
        if not query:
            return []
        scored: list[tuple[int, str]] = []
        for _role, content in candidates:
            overlap = len(query & _tokens(content))
            if overlap:
                scored.append((overlap, content))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:_EPISODIC_K]]

    def _fit_budget(self, ctx: MemoryContext) -> None:
        """Rogne le contexte au budget de tokens : on garde les faits, on lâche
        d'abord l'historique le plus ancien, puis les souvenirs les moins proches."""
        while _estimate_tokens(ctx.render()) > self.token_budget and ctx.history:
            ctx.history.pop(0)
        while _estimate_tokens(ctx.render()) > self.token_budget and ctx.episodic:
            ctx.episodic.pop()
