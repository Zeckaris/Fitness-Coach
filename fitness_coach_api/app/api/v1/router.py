from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.user import router as user_router
from app.api.v1.endpoints.auth import router as login_router


router= APIRouter()
router.include_router(health_router)
router.include_router(user_router)
router.include_router(login_router)
