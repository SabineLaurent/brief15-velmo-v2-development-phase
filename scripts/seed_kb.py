"""Ingestion de la FAQ Velmo (kb/docs/*.md) dans Chroma.

Usage : uv run python scripts/seed_kb.py
Nécessite l'extra `vector` (chromadb + sentence-transformers) et un service Chroma.
"""

from __future__ import annotations

import os
from pathlib import Path

KB_DOCS_DIR = Path(__file__).resolve().parent.parent / "kb" / "docs"


def main() -> None:
    import chromadb
    from chromadb.utils import embedding_functions
    from dotenv import load_dotenv
    from sentence_transformers import SentenceTransformer

    from velmo.config import chroma_host_port

    load_dotenv()
    model_name = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

    host, port = chroma_host_port() or ("chroma", 8000)
    client = chromadb.HttpClient(host=host, port=port)
    embedder = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
    collection = client.get_or_create_collection("velmo_faq", embedding_function=embedder)

    # Garde-fou : au-delà de sa fenêtre, le modèle tronque la fin du texte
    # SILENCIEUSEMENT (queue jamais vectorisée, donc introuvable). On échoue tôt
    # et fort plutôt que d'indexer un document à moitié cherchable — le jour où
    # une fiche dépasse la limite, il faudra la découper (chunking) avant.
    model = SentenceTransformer(model_name)
    token_limit = model.max_seq_length

    docs, ids, metas = [], [], []
    for path in sorted(KB_DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        n_tokens = len(model.tokenizer.encode(text))
        if n_tokens > token_limit:
            raise SystemExit(
                f"{path.name} : {n_tokens} tokens > fenêtre {token_limit} du modèle "
                f"{model_name} — à découper (chunking) avant indexation."
            )
        docs.append(text)
        ids.append(path.stem)
        metas.append({"source": path.name})

    collection.upsert(documents=docs, ids=ids, metadatas=metas)
    print(f"FAQ ingérée dans Chroma : {len(docs)} documents.")


if __name__ == "__main__":
    main()
