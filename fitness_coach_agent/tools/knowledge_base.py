"""
search_fitness_knowledge_base — V3's RAG retrieval tool.

V3 scope: free-text semantic search over the AstraDB vector store populated
by rag/ingest.py. Unlike search_workout_library (structured, deterministic
filtering over a curated JSON), this tool is for open-ended questions where
the answer requires real explanation/reasoning grounded in the source books
- not a lookup of a single concrete exercise.

Use this for: "why does X hurt", "what is progressive overload", "how do I
warm up before squats", nutrition principles, programming/periodization
concepts, injury/pain guidance that needs real explanation.

Do NOT use this for: "give me a chest exercise" - that's search_workout_library's
job (concrete, filterable, controlled data).
"""

from langchain_core.tools import tool

from rag.vectorstore import get_vectorstore

# Number of chunks to retrieve per query.
TOP_K = 4


@tool
def search_fitness_knowledge_base(query: str) -> str:
    """
    Search the fitness knowledge base for guidance on
    training principles, nutrition, injury/pain, mobility, and programming.

    Use this for open-ended or "why/how" questions that need real
    explanation - not for finding a specific exercise (use
    search_workout_library for that instead).

    Args:
        query: a natural-language question or topic, e.g. "why does my knee
               hurt when I squat" or "how much protein do I need"

    Returns:
        Relevant passages from the source books, each labeled with which
        book it came from, or a message if nothing relevant was found.
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