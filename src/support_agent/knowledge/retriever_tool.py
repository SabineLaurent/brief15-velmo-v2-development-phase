"""Expose the FAQ as a tool the agent can call on demand (agentic RAG).

The tool's docstring is what the LLM reads to decide *when* to use it — so it is
written as an instruction to the model, not just developer documentation.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool
from langchain_core.vectorstores import VectorStore


def build_faq_tool(vector_store: VectorStore, k: int = 4) -> BaseTool:
    """Wrap the FAQ vector store into a `search_faq` retrieval tool.

    Args:
        vector_store: The populated FAQ vector store to search.
        k: How many chunks to return per query.
    """

    @tool
    def search_faq(query: str) -> str:
        """Search the customer-support FAQ knowledge base.

        Use this for any factual question about orders, delivery, returns,
        refunds, payment, accounts, or warranty. Always search before answering
        such questions — do not rely on prior knowledge.
        """
        results = vector_store.similarity_search(query, k=k)
        if not results:
            return "No relevant FAQ entry found."
        return "\n\n---\n\n".join(
            f"[source: {doc.metadata.get('source', 'unknown')}]\n{doc.page_content}"
            for doc in results
        )

    return search_faq
