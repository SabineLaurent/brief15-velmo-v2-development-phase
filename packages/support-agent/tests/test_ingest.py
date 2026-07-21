"""Integration tests for the persistent FAQ index — offline, no provider key.

A fake deterministic embedding stands in for the real provider AND counts its
calls, which is what lets us assert the whole point of the feature: a second
startup must embed nothing at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from support_agent.config import Settings
from support_agent.knowledge import ingest


class CountingEmbeddings(Embeddings):
    """Deterministic embeddings that record how often they are called.

    Deterministic so results are reproducible; the vector only has to separate
    documents, not be meaningful.
    """

    def __init__(self) -> None:
        self.embed_documents_calls = 0
        self.embed_query_calls = 0

    def _vector(self, text: str) -> list[float]:
        return [float(len(text)), float(text.count("a")), float(sum(map(ord, text[:8])))]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embed_documents_calls += 1
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls += 1
        return self._vector(text)


@pytest.fixture
def faq_dir(tmp_path: Path) -> Path:
    faq = tmp_path / "faq"
    faq.mkdir()
    (faq / "livraison.md").write_text(
        "# Livraison\n\nLa livraison standard prend 2 à 4 jours ouvrés.",
        encoding="utf-8",
    )
    (faq / "retours.md").write_text(
        "# Retours\n\nVous disposez de 30 jours pour retourner un article.",
        encoding="utf-8",
    )
    return faq


@pytest.fixture
def settings(tmp_path: Path, faq_dir: Path) -> Settings:
    return Settings(
        knowledge_dir=str(faq_dir),
        knowledge_index_dir=str(tmp_path / "chroma"),
        embeddings_provider="fake",
        embeddings_model="fake-embed",
    )


@pytest.fixture
def embeddings(monkeypatch: pytest.MonkeyPatch) -> CountingEmbeddings:
    """Swap the provider factory for a fake, so no network call can happen."""
    fake = CountingEmbeddings()
    monkeypatch.setattr(ingest, "get_embeddings", lambda _settings: fake)
    return fake


def test_first_build_indexes_and_is_searchable(
    settings: Settings, embeddings: CountingEmbeddings
) -> None:
    store = ingest.build_vector_store(settings)

    assert embeddings.embed_documents_calls == 1
    results = store.similarity_search("livraison", k=1)
    assert results and results[0].metadata["source"] in {"livraison.md", "retours.md"}


def test_second_startup_reuses_the_index_without_embedding(
    settings: Settings, embeddings: CountingEmbeddings
) -> None:
    """The whole point: a warm start must not embed anything."""
    ingest.build_vector_store(settings)
    calls_after_build = embeddings.embed_documents_calls

    ingest.build_vector_store(settings)

    assert embeddings.embed_documents_calls == calls_after_build


def test_editing_the_faq_triggers_a_rebuild(
    settings: Settings, faq_dir: Path, embeddings: CountingEmbeddings
) -> None:
    ingest.build_vector_store(settings)
    calls_after_build = embeddings.embed_documents_calls

    (faq_dir / "livraison.md").write_text("# Livraison\n\nDésormais 24 h.", encoding="utf-8")
    store = ingest.build_vector_store(settings)

    assert embeddings.embed_documents_calls > calls_after_build
    # Assert on what the index CONTAINS, not on similarity ranking: the fake
    # embedding is deterministic but meaningless, so ordering proves nothing.
    indexed = " ".join(store.get()["documents"])
    assert "24 h" in indexed
    assert "2 à 4 jours" not in indexed


def test_rebuild_does_not_duplicate_chunks(
    settings: Settings, embeddings: CountingEmbeddings
) -> None:
    """`add_documents` appends on a persistent collection: ids must prevent piling up."""
    store = ingest.build_vector_store(settings)
    count_after_first = len(store.get()["ids"])

    ingest.build_vector_store(settings)  # warm, no write
    (Path(settings.knowledge_index_dir) / ingest.FINGERPRINT_FILENAME).unlink()
    store = ingest.build_vector_store(settings)  # forced rebuild

    assert len(store.get()["ids"]) == count_after_first


def test_deleting_the_index_directory_forces_a_rebuild(
    settings: Settings, embeddings: CountingEmbeddings
) -> None:
    """`make reindex` semantics: the fingerprint lives inside, so it goes too.

    Chroma caches one client per directory *per process*, and that cached client
    keeps handles on files we are about to delete — wiping the directory under it
    yields "readonly database". `make reindex` runs with the app stopped, so a
    fresh process is the real-world case; here we clear the cache to reproduce it.
    """
    import shutil

    from chromadb.api.shared_system_client import SharedSystemClient

    ingest.build_vector_store(settings)
    calls_after_build = embeddings.embed_documents_calls

    SharedSystemClient.clear_system_cache()  # stand in for "restart the process"
    shutil.rmtree(settings.knowledge_index_dir)
    ingest.build_vector_store(settings)

    assert embeddings.embed_documents_calls > calls_after_build


def test_empty_knowledge_dir_raises(tmp_path: Path, embeddings: CountingEmbeddings) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    settings = Settings(
        knowledge_dir=str(empty), knowledge_index_dir=str(tmp_path / "chroma-empty")
    )
    with pytest.raises(FileNotFoundError, match="No FAQ"):
        ingest.build_vector_store(settings)
