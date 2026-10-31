from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database.mongodb import create_mongodb_client, get_database, close_mongodb_client
from app.api.v1.router import router as api_v1_router

@asynccontextmanager
async def lifespan(app : FastAPI):
    
    client=create_mongodb_client()
    await client.admin.command("ping")
    app.state.mongodb_client= client
    app.state.mongodb= get_database(app.state.mongodb_client)
    
    yield
    
    await close_mongodb_client(app.state.mongodb_client)
    
    
app= FastAPI(
    title= "Fitness Coach API",
    lifespan=lifespan
)

app.include_router(api_v1_router, prefix="/api/v1")

