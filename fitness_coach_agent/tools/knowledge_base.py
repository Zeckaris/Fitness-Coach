"""
search_fitness_knowledge_base — V3 RAG retrieval tool.

Semantic search over the fitness knowledge base.

Use for explanations about training, nutrition, mobility, injury, and
programming concepts. Not for retrieving specific exercises.
"""

from langchain_core.tools import tool

from rag.vectorstore import get_vectorstore

# Number of chunks to retrieve per query.
TOP_K = 4


@tool
def search_fitness_knowledge_base(query: str) -> str:
    """
    Search the fitness knowledge base for relevant guidance.

    Use for fitness explanations and concepts.

    Args:
        query: Natural-language topic or question.

    Returns:
        Relevant source passages or a no-results message.
    """
    store = get_vectorstore()
    results = store.similarity_search(query, k=TOP_K)

    if not results:
        return f"No relevant information found in the knowledge base for '{query}'."

    lines = []
    for doc in results:
        source = doc.metadata.get("source", "unknown source")
        page = doc.metadata.get("page")
        page_str = f", page {page}" if page is not None else ""
        lines.append(f"[From: {source}{page_str}]\n{doc.page_content}")

    return "\n\n".join(lines)


# Quick manual test: python -m tools.knowledge_base
if __name__ == "__main__":
    print(search_fitness_knowledge_base.invoke({"query": "how much protein should I eat per day"}))