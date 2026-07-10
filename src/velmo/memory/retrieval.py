# ===============================================================================
# VELMO’S MEMORY BRAIN
# Récupération épisodique, stratégie de rappel des souvenirs anciens
# ===============================================================================

"""Étage épisodique de la mémoire long terme : indexation + rappel des souvenirs.

Isolé derrière le protocole `EpisodicRetriever` (deux méthodes symétriques :
`.index(...)` à l'écriture, `.recall(...)` à la lecture). Le repli hors-ligne est
le recouvrement lexical (`LexicalRetriever`, qui lit les épisodes déjà en SQL) ;
`ChromaEpisodicRetriever` fournit la recherche sémantique sans toucher au manager,
en implémentant le même protocole. `user_id` figure partout car l'isolation par
utilisateur est le contrat non négociable de tout backend réel.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol
from uuid import uuid4

from ..config import chroma_host_port, episodic_backend, warn_backend_unavailable
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
    """Contrat de l'étage épisodique : indexer un tour, puis rappeler les proches."""

    def index(self, user_id: str, role: str, content: str) -> None:
        """Mémorise un tour en vue d'un rappel futur."""
        ...

    def recall(
        self, user_id: str, message: str, candidates: list[Turn], k: int
    ) -> list[str]:
        """Remonte les `k` souvenirs anciens les plus proches de `message`."""
        ...


class LexicalRetriever:
    """Rappel par recouvrement lexical (repli hors-ligne, sans dépendance réseau).

    Score chaque souvenir candidat par le nombre de mots signifiants partagés avec
    le message courant, et remonte les `k` meilleurs. `user_id` est ignoré : les
    candidats sont déjà filtrés par utilisateur en amont par l'orchestrateur.
    """

    def index(self, user_id: str, role: str, content: str) -> None:
        """Sans effet : le repli lexical lit à la volée les épisodes déjà
        persistés en SQL, il n'a aucun index vectoriel à alimenter."""

    def recall(
        self, user_id: str, message: str, candidates: list[Turn], k: int
    ) -> list[str]:
        query = _tokens(message)
        if not query:
            return []
        scored: list[tuple[int, str]] = []
        for turn in candidates:
            overlap = len(query & _tokens(turn.content))
            if overlap:
                scored.append((overlap, turn.content))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:k]]


class ChromaEpisodicRetriever:
    """Rappel épisodique sémantique via Chroma (embeddings e5 multilingues).

    Satisfait `EpisodicRetriever` par typage structurel. Deux garanties :

    - isolation stricte par `user_id` : métadonnée posée à l'indexation ET filtre
      de métadonnée à la requête — c'est le contrat R3 au niveau vectoriel ;
    - `candidates` sert de liste blanche au `recall` : Chroma indexe tous les
      tours, mais on ne ressort que les souvenirs *anciens* fournis par
      l'orchestrateur, jamais un tour déjà présent dans l'historique court terme.
    """

    # Le filtrage par liste blanche peut écarter des résultats : on demande
    # plus que `k` à Chroma pour survivre au post-filtrage.
    _OVERFETCH = 4

    def __init__(self, collection: Any) -> None:  # type Chroma dynamique
        self._collection = collection

    def index(self, user_id: str, role: str, content: str) -> None:
        self._collection.add(
            ids=[uuid4().hex],
            documents=[content],
            metadatas=[{"user_id": user_id, "role": role}],
        )

    def recall(
        self, user_id: str, message: str, candidates: list[Turn], k: int
    ) -> list[str]:
        if not candidates:
            return []  # parité avec le lexical : rien d'ancien → rien à rappeler
        allowed = {turn.content for turn in candidates}
        result = self._collection.query(
            query_texts=[message],
            n_results=k * self._OVERFETCH,
            where={"user_id": user_id},  # ← LA garantie d'isolation
        )
        docs = result.get("documents", [[]])[0]
        kept: list[str] = []
        for doc in docs:  # ordre de pertinence de Chroma préservé
            if doc in allowed and doc not in kept:
                kept.append(doc)
            if len(kept) == k:
                break
        return kept


def get_episodic_retriever() -> EpisodicRetriever:
    """Si `VELMO_EPISODIC=chroma` et Chroma joignable, renvoie le rappel
    sémantique ; sinon repli sur le recouvrement lexical hors-ligne.

    Même chaîne de replis individuels que `get_kb` : variable non positionnée,
    `CHROMA_URL` absent ou `chromadb` non installé → `LexicalRetriever`.
    """
    if episodic_backend() != "chroma":
        return LexicalRetriever()
    endpoint = chroma_host_port()
    if endpoint is None:
        warn_backend_unavailable("mémoire épisodique Chroma", "CHROMA_URL absent")
        return LexicalRetriever()
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        warn_backend_unavailable(
            "mémoire épisodique Chroma", "dépendance chromadb absente"
        )
        return LexicalRetriever()

    client = chromadb.HttpClient(host=endpoint[0], port=endpoint[1])
    embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    )
    collection = client.get_or_create_collection(
        "velmo_episodic", embedding_function=embedder
    )
    return ChromaEpisodicRetriever(collection)
