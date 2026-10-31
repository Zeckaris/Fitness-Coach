from fastapi import APIRouter, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase
from app.dependencies.database import get_db

router= APIRouter()
@router.get("/health")
async def health_check(db:AsyncDatabase= Depends(get_db)):
    try:
        await db.command("ping")
        return {"status" : "healthy", "database" : "connected"}
    except Exception:
        raise HTTPException(
            status=503,
            detail= "Database connection unavailable"
        )
