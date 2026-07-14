"""
MongoDB connection for check-in persistence.

V4 scope: one function, get_checkins_collection(), returns a configured
pymongo Collection for storing daily check-ins. Mirrors rag/vectorstore.py's
connection pattern.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
load_dotenv()

from pymongo import MongoClient
from pymongo.collection import Collection

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "fitnesscoach")
MONGO_CHECKINS_COLLECTION = os.environ.get("MONGO_CHECKINS_COLLECTION", "checkins")


@lru_cache(maxsize=1)
def get_mongo_client() -> MongoClient:
    if not MONGO_URI:
        raise ValueError(
            "MONGO_URI not found in environment. Check your .env file."
        )
    return MongoClient(MONGO_URI)


def get_checkins_collection() -> Collection:
    """
    Returns the MongoDB collection used to store daily check-ins.
    One document per (user_id, date), upserted as new info comes in.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_CHECKINS_COLLECTION]


# Confirm the connection works
if __name__ == "__main__":
    collection = get_checkins_collection()
    print(f"Connected to MongoDB collection '{collection.name}'")
    print(f"Existing check-in count: {collection.count_documents({})}")