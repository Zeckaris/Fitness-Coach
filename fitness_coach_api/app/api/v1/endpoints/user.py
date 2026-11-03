from app.schemas.user import UserCreate, UserResponse
from fastapi import APIRouter, HTTPException, Depends, status
from pymongo.asynchronous.database import AsyncDatabase
from app.dependencies.database import get_db
from pymongo.errors import DuplicateKeyError
from app.repositories.user_repository import create_user, get_user_by_email
from app.utils.responses import success_response, error_response
from fastapi.responses import JSONResponse

router= APIRouter()

@router.post("/user")
async def user_create(user:UserCreate, db: AsyncDatabase = Depends(get_db)):
    try:
        new_user= await create_user(db,user.email, user.password)
        res=UserResponse(
            id= str(new_user.id),
            email= new_user.email,
            created_at= new_user.created_at,
            last_login= new_user.last_login,
        )
        sr= success_response(data=res, status= status.HTTP_201_CREATED)
        return JSONResponse(
            content= sr.model_dump(mode="json"),
            status_code= sr.status
        )
    except (ValueError, DuplicateKeyError) as err:
        er= error_response(message= str(err), status= status.HTTP_409_CONFLICT)
        return JSONResponse(
            content= er.model_dump(mode= "json"),
            status_code= er.status
        )
    
    

