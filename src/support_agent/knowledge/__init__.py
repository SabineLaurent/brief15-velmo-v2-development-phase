"""Knowledge base layer: FAQ ingestion (RAG) and retrieval tooling."""

from support_agent.knowledge.ingest import build_vector_store
from support_agent.knowledge.retriever_tool import build_faq_tool

__all__ = ["build_vector_store", "build_faq_tool"]
