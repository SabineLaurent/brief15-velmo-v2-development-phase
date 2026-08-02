"""FAQ knowledge base ingestion: load -> split -> embed -> index.

This is the classic RAG pipeline. We read the FAQ markdown files, cut them into
overlapping chunks (so a relevant passage isn't split across a boundary), embed
each chunk, and store everything in a vector store we can search by similarity.

For now the vector store lives in memory (rebuilt at each startup). Swapping to a
persistent store (Chroma, pgvector, Azure AI Search...) later means changing this
file only — the agent code never changes.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from support_agent.config import Settings, get_settings
from support_agent.llm.embeddings import get_embeddings


def _load_faq_documents(knowledge_dir: Path) -> list[Document]:
    """Read every `*.md` file in the knowledge directory as a Document."""
    documents: list[Document] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        documents.append(Document(page_content=text, metadata={"source": path.name}))
    return documents


def build_vector_store(settings: Settings | None = None) -> InMemoryVectorStore:
    """Build and populate the FAQ vector store from the knowledge directory."""
    settings = settings or get_settings()
    knowledge_dir = Path(settings.knowledge_dir)

    documents = _load_faq_documents(knowledge_dir)
    if not documents:
        raise FileNotFoundError(
            f"No FAQ '*.md' files found in {knowledge_dir!r}. "
            f"Check KNOWLEDGE_DIR in your .env."
        )

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
    chunks = splitter.split_documents(documents)

    store = InMemoryVectorStore(embedding=get_embeddings(settings))
    store.add_documents(chunks)
    return store
