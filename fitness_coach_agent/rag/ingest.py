"""
Document ingestion pipeline for the RAG knowledge base.

V3 scope: load PDFs from data/documents/ -> chunk -> embed -> store in
AstraDB.
"""

from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.vectorstore import get_vectorstore

DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"

# Chunk size/overlap in characters 1000/200 
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def load_and_chunk_documents() -> list:
    """
    loads each one page by page, tags every page with its source
    filename, then splits into overlapping chunks. Metadata (source, page)
    survives the split, so every chunk knows which book/page it came from.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    all_chunks = []
    pdf_paths = sorted(DOCUMENTS_DIR.rglob("*.pdf"))

    if not pdf_paths:
        print(f"No PDFs found under {DOCUMENTS_DIR}")
        return all_chunks

    for pdf_path in pdf_paths:
        print(f"Loading {pdf_path.name}...")
        loader = PyPDFLoader(str(pdf_path))
        pages = loader.load()

        source_name = pdf_path.stem
        for page in pages:
            page.metadata["source"] = source_name

        chunks = splitter.split_documents(pages)
        print(f"  -> {len(pages)} pages -> {len(chunks)} chunks")
        all_chunks.extend(chunks)

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