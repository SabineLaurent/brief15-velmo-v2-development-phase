"""Mémoire long terme épisodique : backend Chroma (prod) et repli local (hors-ligne).

Miroir de `kb_store.py` (même patron : backend réel sélectionné par variable
d'environnement, repli hors-ligne pour que les tests tournent sans service
externe). Les deux backends exposent `add(user_id, text)` et
`search(user_id, query, k) -> list[str]`.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Protocol


class EpisodicStore(Protocol):
    """Interface minimale d'un backend de mémoire long terme épisodique."""

    def add(self, user_id: str, text: str) -> None: ...
    def search(self, user_id: str, query: str, k: int = 3) -> list[str]: ...
    def all_for(self, user_id: str) -> list[str]: ...
    def forget(self, user_id: str, target: str) -> int: ...


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


class LocalEpisodicStore:
    """Recherche par recouvrement de tokens, en RAM, hors-ligne."""

    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}

    def add(self, user_id: str, text: str) -> None:
        self._store.setdefault(user_id, []).append(text)

    def search(self, user_id: str, query: str, k: int = 3) -> list[str]:
        q = _tokens(query)
        scored = [
            (len(q & _tokens(text)), text) for text in self._store.get(user_id, [])
        ]
        scored = [(score, text) for score, text in scored if score > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [text for _, text in scored[:k]]

    def all_for(self, user_id: str) -> list[str]:
        return list(self._store.get(user_id, []))

    def forget(self, user_id: str, target: str) -> int:
        target_l = target.lower()
        texts = self._store.get(user_id, [])
        kept = [text for text in texts if target_l not in text.lower()]
        removed = len(texts) - len(kept)
        self._store[user_id] = kept
        return removed


class ChromaEpisodicStore:
    """Recherche sémantique via une collection Chroma dédiée à la mémoire."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def add(self, user_id: str, text: str) -> None:
        self._collection.add(
            ids=[uuid.uuid4().hex],
            documents=[text],
            metadatas=[{"user_id": user_id}],
        )

    def search(self, user_id: str, query: str, k: int = 3) -> list[str]:
        result = self._collection.query(
            query_texts=[query], n_results=k, where={"user_id": user_id}
        )
        return list(result.get("documents", [[]])[0])

    def all_for(self, user_id: str) -> list[str]:
        # Chroma n'a pas d'équivalent simple à un "SELECT *" par métadonnée
        # sans pagination explicite ; hors périmètre pour `inspect()` ici.
        return []

    def forget(self, user_id: str, target: str) -> int:
        match = self._collection.get(
            where={"user_id": user_id}, where_document={"$contains": target}
        )
        ids = match.get("ids", [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)


def get_episodic_store() -> EpisodicStore:
    """Renvoie le backend Chroma si configuré et disponible, sinon le repli local."""
    if not os.getenv("CHROMA_URL"):
        return LocalEpisodicStore()
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        return LocalEpisodicStore()

    client = chromadb.HttpClient(host="chroma", port=8000)
    embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    )
    collection = client.get_or_create_collection("velmo_episodic", embedding_function=embedder)
    return ChromaEpisodicStore(collection)
