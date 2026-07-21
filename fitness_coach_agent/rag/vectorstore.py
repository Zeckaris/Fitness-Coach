"""
AstraDB vector store connection.

V3 scope: one function that returns a configured AstraDBVectorStore, using
the embedding model from rag/embeddings.py.
"""

import os
from dotenv import load_dotenv
load_dotenv()
from langchain_astradb import AstraDBVectorStore

from rag.embeddings import get_embeddings

ASTRA_DB_API_ENDPOINT = os.environ.get("ASTRA_DB_API_ENDPOINT")
ASTRA_DB_APPLICATION_TOKEN = os.environ.get("ASTRA_DB_APPLICATION_TOKEN")
ASTRA_DB_COLLECTION_NAME = os.environ.get("ASTRA_DB_COLLECTION_NAME", "fitness_knowledge_base")


def get_vectorstore() -> AstraDBVectorStore:
    """
    Connects to (or creates, if it doesn't exist yet) the AstraDB collection
    used as our knowledge base.
    """
    if not ASTRA_DB_API_ENDPOINT or not ASTRA_DB_APPLICATION_TOKEN:
        raise ValueError(
            "ASTRA_DB_API_ENDPOINT / ASTRA_DB_APPLICATION_TOKEN not found in "
            "environment. Check your .env file."
        )

    return AstraDBVectorStore(
        embedding=get_embeddings(),
        collection_name=ASTRA_DB_COLLECTION_NAME,
        api_endpoint=ASTRA_DB_API_ENDPOINT,
        token=ASTRA_DB_APPLICATION_TOKEN,
    )



# Confirm the connection works - does NOT add any data
if __name__ == "__main__":
    store = get_vectorstore()
    print(f"Connected to AstraDB collection '{ASTRA_DB_COLLECTION_NAME}'")