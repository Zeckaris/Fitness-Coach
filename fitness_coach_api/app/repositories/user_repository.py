from pymongo.asynchronous.database import AsyncDatabase
from app.models.user import UserModel


async def get_user_by_email(db:AsyncDatabase, email : str) -> UserModel| None:
    user_document= await db["user"].find_one({ "email" : email})
    
    if not user_document:
        return None
    return UserModel.model_validate(user_document)