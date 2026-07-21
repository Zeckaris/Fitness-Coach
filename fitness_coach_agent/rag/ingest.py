"""
Document ingestion pipeline for the RAG knowledge base — V4.

Changes from V3:
- Uses rag.preprocessor instead of raw PyPDFLoader
- Pre-processing includes: cleaning, table structuring, exercise restructuring,
  semantic chunking with token budget (300-500 tokens target)
- Same output format: Document objects with .page_content and .metadata
"""

from pathlib import Path

from rag.preprocessor import preprocess_all_pdfs
from rag.vectorstore import get_vectorstore

DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"


def load_and_chunk_documents() -> list:
    """
    V4: Uses preprocessor pipeline instead of raw PyPDFLoader.

    The preprocessor handles:
      - PDF loading (via PyPDFLoader internally)
      - Text cleaning (headers, footers, captions, citations)
      - Table detection and markdown conversion
      - Exercise guide restructuring
      - Semantic chunking with ~400-500 token budget
      - Metadata enrichment (source, doc_type, chunk_index, etc.)

    Returns Document objects ready for embedding.
    """
    all_chunks = preprocess_all_pdfs(DOCUMENTS_DIR)
    return all_chunks


def ingest():
    chunks = load_and_chunk_documents()
    if not chunks:
        print("Nothing to ingest.")
        return

    print(f"\nEmbedding and storing {len(chunks)} chunks in AstraDB...")
    store = get_vectorstore()
    store.add_documents(chunks)
    print("Ingestion complete.")


# Run: python -m rag.ingest
if __name__ == "__main__":
    ingest()