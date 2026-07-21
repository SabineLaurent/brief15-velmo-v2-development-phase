"""FAQ knowledge base ingestion: load -> split -> embed -> index.

This is the classic RAG pipeline. We read the FAQ markdown files, cut them into
overlapping chunks (so a relevant passage isn't split across a boundary), embed
each chunk, and store everything in a vector store we can search by similarity.

The index is **persistent** (Chroma, embedded mode) and lives under `TEMP/`: it
is a rebuildable projection of the FAQ files, not source content. Embedding every
chunk on each startup was pure waste — with the frontend running under `-w`, it
was replayed on every file save.

Persisting an index buys a new risk: serving vectors that no longer match the
source. That is what `fingerprint.py` guards — see its docstring. The rule here:
**reuse only when we can prove validity, rebuild otherwise.**

Swapping the backend stays a one-file change. Production runs Chroma as a
*server*: same `Chroma` class, `host`/`port` instead of `persist_directory`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from support_agent.config import Settings, get_settings
from support_agent.knowledge.fingerprint import (
    compute_fingerprint,
    read_fingerprint,
    write_fingerprint,
)
from support_agent.llm.embeddings import get_embeddings

logger = logging.getLogger(__name__)

# Chunking parameters. Not exposed as settings — nobody tunes an overlap from a
# `.env` — but they ARE part of the fingerprint: different cuts, different chunks.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

COLLECTION_NAME = "faq"

# The fingerprint lives INSIDE the index directory on purpose: deleting that
# directory by hand must also drop the fingerprint. Stored outside, it would
# survive the deletion and claim a now-empty index is valid — the agent would
# lose its FAQ silently.
FINGERPRINT_FILENAME = "faq_index.meta.json"


def _load_faq_documents(knowledge_dir: Path) -> list[Document]:
    """Read every `*.md` file in the knowledge directory as a Document."""
    documents: list[Document] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # `source` metadata lets the agent cite where an answer comes from.
        documents.append(Document(page_content=text, metadata={"source": path.name}))
    return documents


def _chunk_ids(chunks: list[Document]) -> list[str]:
    """Stable ids so re-indexing overwrites instead of piling up duplicates.

    `add_documents` on a *persistent* collection appends by default: without
    explicit ids, every startup would add another copy of every chunk.
    """
    return [f"{chunk.metadata.get('source', 'unknown')}:{i}" for i, chunk in enumerate(chunks)]


def build_vector_store(settings: Settings | None = None) -> VectorStore:
    """Return the FAQ vector store, reusing the persisted index when it is valid.

    Reuses the stored index when its fingerprint matches the current FAQ files,
    embedding model and chunking — otherwise rebuilds it from scratch.

    Args:
        settings: Optional settings override (handy for tests).
    """
    settings = settings or get_settings()
    knowledge_dir = Path(settings.knowledge_dir)
    index_dir = Path(settings.knowledge_index_dir)

    fingerprint = compute_fingerprint(
        knowledge_dir,
        embeddings_provider=settings.embeddings_provider,
        embeddings_model=settings.embeddings_model,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    store = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(settings),
        persist_directory=str(index_dir),
    )

    fingerprint_path = index_dir / FINGERPRINT_FILENAME
    if read_fingerprint(fingerprint_path) == fingerprint:
        logger.info("FAQ index reused from %s (fingerprint match, no embedding call).", index_dir)
        return store

    logger.info("Rebuilding FAQ index in %s (missing or stale fingerprint)...", index_dir)

    documents = _load_faq_documents(knowledge_dir)
    if not documents:
        raise FileNotFoundError(
            f"No FAQ '*.md' files found in {knowledge_dir!r}. "
            f"Check KNOWLEDGE_DIR in your .env."
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks = splitter.split_documents(documents)

    # Drop whatever was there: a stale fingerprint means the old vectors are not
    # trustworthy, and a changed FAQ may have *fewer* chunks than before.
    store.reset_collection()
    store.add_documents(chunks, ids=_chunk_ids(chunks))

    # Written last, on purpose: if anything above fails, no fingerprint is
    # recorded and the next startup rebuilds rather than trusting a partial index.
    write_fingerprint(
        fingerprint_path,
        fingerprint,
        context={
            "embeddings_model": f"{settings.embeddings_provider}:{settings.embeddings_model}",
            "chunking": f"size={CHUNK_SIZE} overlap={CHUNK_OVERLAP}",
            "documents": str(len(documents)),
            "chunks": str(len(chunks)),
        },
    )
    logger.info("FAQ index rebuilt: %d documents -> %d chunks.", len(documents), len(chunks))
    return store
