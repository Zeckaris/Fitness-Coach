"""
Embedding model configuration for the RAG pipeline.

V3 scope: one function, one model. HuggingFace's all-MiniLM-L6-v2 - free,
runs locally (no API key/cost), 384-dim vectors. Used by both embedding
documents at load time and the retrieval tool (embedding the query).
"""

from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384

#@lru_cache(maxsize=1) means the model is loaded from disk only on the first call
@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    """
    Returns the embedding model used for both ingestion and retrieval.
    Must be the SAME model on both sides.
    """
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)


# Quick manual test: python -m rag.embeddings
if __name__ == "__main__":
    import time

    start = time.perf_counter()
    model = get_embeddings()
    print(f"First call (loads model): {time.perf_counter() - start:.2f}s")

    start = time.perf_counter()
    model_again = get_embeddings()
    print(f"Second call (should be ~instant, cached): {time.perf_counter() - start:.4f}s")
    print(f"Same instance? {model is model_again}")

    vector = model.embed_query("shoulder pain exercise alternatives")
    print(f"Embedded a test query into a {len(vector)}-dimensional vector")
    print(f"First 5 values: {vector[:5]}")