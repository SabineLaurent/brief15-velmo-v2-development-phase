"""Mémoire de l'agent Velmo : contexte court terme et mémoire long terme.

Surface publique stable consommée par l'agent et la suite d'acceptance.
Étape 1b (courante) : court terme (tampon en RAM borné par `token_budget`) +
long terme épisodique (recherche par similarité au-delà de la fenêtre court
terme, via `episodic.get_episodic_store()`). Les faits durables (long terme
factuel) et le droit à l'oubli sont des étapes ultérieures ;
`remember_fact`/`forget` restent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .episodic import get_episodic_store

Turn = tuple[str, str]  # (role, content)

# Nombre de souvenirs épisodiques injectés par lecture : borne fixe, pas
# comptée dans `token_budget` (simplification assumée, voir la note de
# cours de l'étape 1b).
_EPISODIC_K = 3

# Heuristique volontairement grossière (pas de tokenizer réel) : ~4 caractères
# par token, suffisante pour borner un tampon court terme.
_CHARS_PER_TOKEN = 4


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
    """Orchestre la mémoire court terme et long terme, isolée par utilisateur.

    Court terme : tampon en RAM (`self._history`), un par `user_id`, perdu à
    la fin du process. Long terme épisodique : `self._episodic`, un backend
    interrogeable par similarité (Chroma en prod, repli local hors-ligne),
    qui retrouve les souvenirs pertinents même sortis du tampon court terme.
    Les faits durables (long terme factuel) sont une étape ultérieure.
    """

    def __init__(self, *, token_budget: int = 2000) -> None:
        self.token_budget = token_budget
        self._history: dict[str, list[Turn]] = {}
        self._episodic = get_episodic_store()

    def read(self, user_id: str, message: str) -> MemoryContext:
        """Reconstitue le contexte mémoire pertinent pour `message`."""
        history = list(self._history.get(user_id, []))
        already_present = {f"{role}: {content}" for role, content in history}
        hits = self._episodic.search(user_id, message, k=_EPISODIC_K)
        episodic = [hit for hit in hits if hit not in already_present]
        return MemoryContext(history=history, episodic=episodic)

    def write(self, user_id: str, user_message: str, assistant_message: str) -> None:
        """Met à jour la mémoire à partir d'un échange."""
        turns = self._history.setdefault(user_id, [])
        turns.append(("user", user_message))
        turns.append(("assistant", assistant_message))
        self._trim_to_budget(turns)
        self._episodic.add(user_id, f"user: {user_message}")
        self._episodic.add(user_id, f"assistant: {assistant_message}")

    def remember_fact(self, user_id: str, key: str, value: str) -> None:
        """Persiste un fait durable sur l'utilisateur."""
        # Étape ultérieure (mémoire long terme factuelle).
        return None

    def forget(self, user_id: str, target: str) -> int:
        """Supprime les souvenirs correspondant à `target`. Renvoie le nombre supprimé."""
        # Étape ultérieure (droit à l'oubli) : devra aussi purger l'épisodique.
        return 0

    def inspect(self, user_id: str) -> dict:
        """Renvoie l'état mémoire d'un utilisateur (historique, faits, épisodique)."""
        return {
            "history": list(self._history.get(user_id, [])),
            "facts": {},
            "episodic": self._episodic.all_for(user_id),
        }

    def _trim_to_budget(self, turns: list[Turn]) -> None:
        """Rogne `turns` en place : jette les tours les plus anciens en excès."""
        while len(turns) > 1 and self._approx_tokens(turns) > self.token_budget:
            turns.pop(0)

    @staticmethod
    def _approx_tokens(turns: list[Turn]) -> int:
        chars = sum(len(role) + len(content) for role, content in turns)
        return chars // _CHARS_PER_TOKEN
