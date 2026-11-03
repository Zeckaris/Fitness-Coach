from pymongo.asynchronous.database import AsyncDatabase
from app.models.user import UserModel
import bcrypt
from datetime import datetime, timezone
from pymongo.errors import DuplicateKeyError


async def get_user_by_email(db:AsyncDatabase, email : str) -> UserModel| None:
    email= email.strip().lower()
    user_document= await db["users"].find_one({ "email" : email})
    
    if not user_document:
        return None
    return UserModel.model_validate(user_document)

async def create_user(db : AsyncDatabase, email : str, password : str)-> UserModel:
    email= email.strip().lower()
    if await get_user_by_email(db, email):
        raise ValueError("Email already registered")
    
    hashed_password= bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    )
    now= datetime.now(timezone.utc)
    
    
    user_document = {
        "email" : email, 
        "password_hash" : hashed_password,
        "last_login" : None,
        "created_at" : now,
        "updated_at" : now
    }
    
    try:
        result= await db["users"].insert_one(user_document)
        
        created_user= await db["users"].find_one({"_id" : result.inserted_id})
        return UserModel.model_validate(created_user)
    except DuplicateKeyError:
        raise ValueError("Email already registered")
        
        
def verify_password(plain_password:str, hashed_password:bytes)->bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"),hashed_password)