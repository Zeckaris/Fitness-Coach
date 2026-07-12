"""
Embedding model configuration for the RAG pipeline.

V3 scope: one function, one model. HuggingFace's all-MiniLM-L6-v2 - free,
runs locally (no API key/cost), 384-dim vectors. Used by both embedding
documents at load time and the retrieval tool (embedding the query).
"""

from langchain_huggingface import HuggingFaceEmbeddings

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384 


def get_embeddings() -> HuggingFaceEmbeddings:
    """
    Returns the embedding model used for both ingestion and retrieval.
    Must be the SAME model on both sides - embeddings from different models
    aren't comparable, so if you ever change EMBEDDING_MODEL_NAME, you need
    to re-ingest all documents, not just change the retrieval side.
    """
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)


# Quick manual test: python rag/embeddings.py
if __name__ == "__main__":
    model = get_embeddings()
    vector = model.embed_query("shoulder pain exercise alternatives")
    print(f"Embedded a test query into a {len(vector)}-dimensional vector")
    print(f"First 5 values: {vector[:5]}")