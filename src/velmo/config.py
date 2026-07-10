"""Sélecteurs de pile : un interrupteur dédié par brique externe.

Chaque brique choisit son backend indépendamment, via sa propre variable
d'environnement. Le défaut de chacune est le backend léger auto-suffisant
(hors-ligne, sans dépendance réseau) ; la valeur alternative active le backend
réel. Si le backend réel est demandé mais injoignable (dépendance absente,
endpoint non configuré), la brique dégrade *individuellement* vers son repli
léger — le filet echo/SQLite reste garanti.

| Brique    | Variable       | Défaut  | Réel     |
|-----------|----------------|---------|----------|
| LLM       | `VELMO_LLM`    | `echo`  | `kimi`   |
| Mémoire   | `VELMO_MEMORY` | `sqlite`| `postgres` |
| KB / FAQ  | `VELMO_KB`     | `local` | `chroma` |
| Épisodique| `VELMO_EPISODIC`| `lexical`| `chroma` |
| DB métier | `VELMO_DB`     | `sqlite`| `postgres` |
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

_log = logging.getLogger("velmo")


def _backend(var: str, default: str) -> str:
    return os.getenv(var, default).strip().lower()


def llm_backend() -> str:
    """`echo` (repli hors-ligne) ou `kimi` (Azure AI Inference)."""
    return _backend("VELMO_LLM", "echo")


def memory_backend() -> str:
    """`sqlite` (fichier local) ou `postgres` (mémoire faits + épisodes)."""
    return _backend("VELMO_MEMORY", "sqlite")


def kb_backend() -> str:
    """`local` (TF-IDF hors-ligne) ou `chroma` (FAQ sémantique)."""
    return _backend("VELMO_KB", "local")


def episodic_backend() -> str:
    """`lexical` (recouvrement hors-ligne) ou `chroma` (rappel sémantique)."""
    return _backend("VELMO_EPISODIC", "lexical")


def db_backend() -> str:
    """`sqlite` (sampledata en mémoire) ou `postgres` (session métier réelle)."""
    return _backend("VELMO_DB", "sqlite")


def chroma_host_port() -> tuple[str, int] | None:
    """(host, port) depuis `CHROMA_URL` ; `None` si la variable est absente."""
    url = os.getenv("CHROMA_URL")
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.hostname or "chroma", parsed.port or 8000


def warn_backend_unavailable(brick: str, reason: str) -> None:
    """Journalise un repli individuel vers le backend léger."""
    _log.warning("%s : backend réel indisponible (%s) — repli léger.", brick, reason)
