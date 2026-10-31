from pymongo import AsyncMongoClient
from app.core.config import settings


def create_mongodb_client()-> AsyncMongoClient:
    client= AsyncMongoClient(settings.mongodb_uri)
    return client

def get_database(client:AsyncMongoClient):
    db= client.get_database(settings.mongodb_database)
    return db

async def close_mongodb_client(client:AsyncMongoClient):
    await client.close()
    