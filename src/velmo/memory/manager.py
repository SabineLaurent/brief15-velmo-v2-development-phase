# ===============================================================================
# VELMO’S MEMORY BRAIN
# L'orchestrateur
# ===============================================================================

"""Orchestration de la mémoire : reconstitue le contexte pertinent (faits +
souvenirs) et le tient au budget de tokens, en s'appuyant sur `MemoryStore`.

Étage court terme = les derniers tours ; étage long terme épisodique = les tours
plus anciens, remontés par un `EpisodicRetriever` (recouvrement lexical par
défaut). Les faits durables (palier 2) complètent le contexte. Le rappel est une
stratégie injectable : une recherche sémantique (Chroma) pourra remplacer le
lexical sans toucher à cet orchestrateur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .extraction import extract_facts
from .retrieval import EpisodicRetriever, get_episodic_retriever
from .store import MemoryStore, Turn

# Nombre de tours récents gardés tels quels (mémoire court terme).
_RECENT = 15
# Nombre de souvenirs anciens remontés par pertinence (mémoire épisodique).
_EPISODIC_K = 3


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

    def __init__(
        self,
        *,
        token_budget: int = 2000,
        store: MemoryStore | None = None,
        retriever: EpisodicRetriever | None = None,
    ) -> None:
        self.token_budget = token_budget
        self._store = store or MemoryStore()
        self._retriever = retriever or get_episodic_retriever()

    def read(self, user_id: str, message: str) -> MemoryContext:
        """Reconstitue le contexte mémoire pertinent pour `message`."""
        facts = self._store.facts(user_id)
        episodes = self._store.episodes(user_id)

        recent = episodes[-_RECENT:]
        older = episodes[:-_RECENT]  # [] automatiquement si ≤ _RECENT tours
        episodic = self._retriever.recall(user_id, message, older, _EPISODIC_K)

        ctx = MemoryContext(history=recent, facts=facts, episodic=episodic)
        self._fit_budget(ctx)
        return ctx

    def write(self, user_id: str, user_message: str, assistant_message: str) -> None:
        """Met à jour la mémoire à partir d'un échange.

        Classe l'information (cf. architecture à étages) : le tour va au journal
        épisodique SQL (vérité chronologique) et à l'index épisodique du retriever
        (rappel sémantique) ; toute préférence durable détectée est promue en fait
        structuré (source de vérité, toujours chargée, insensible au budget)."""
        self._store.add_episode(user_id, "user", user_message)
        self._store.add_episode(user_id, "assistant", assistant_message)
        self._retriever.index(user_id, "user", user_message)
        self._retriever.index(user_id, "assistant", assistant_message)
        for key, value in extract_facts(user_message).items():
            self._store.upsert_fact(user_id, key, value)

    def remember_fact(self, user_id: str, key: str, value: str) -> None:
        """Persiste un fait durable sur l'utilisateur."""
        self._store.upsert_fact(user_id, key, value)

    def forget(self, user_id: str, target: str) -> int:
        """Supprime les souvenirs correspondant à `target`. Renvoie le nombre supprimé."""
        return self._store.forget(user_id, target)

    def inspect(self, user_id: str) -> dict[str, object]:
        """Renvoie l'état mémoire d'un utilisateur (faits + souvenirs épisodiques)."""
        return {
            "facts": self._store.facts(user_id),
            "episodic": [turn.content for turn in self._store.episodes(user_id)],
        }

    # --- interne -------------------------------------------------------------

    def _fit_budget(self, ctx: MemoryContext) -> None:
        """Rogne le contexte au budget de tokens : on garde les faits, on lâche
        d'abord l'historique le plus ancien, puis les souvenirs les moins proches."""
        while _estimate_tokens(ctx.render()) > self.token_budget and ctx.history:
            ctx.history.pop(0)
        while _estimate_tokens(ctx.render()) > self.token_budget and ctx.episodic:
            ctx.episodic.pop()
