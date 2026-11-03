from fastapi import APIRouter, Depends, status, HTTPException
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase

from app.schemas.auth import LoginRequest, TokenResponse
from app.utils.security import create_access_token
from app.repositories.user_repository import get_user_by_email, verify_password
from app.dependencies.database import get_db
from app.utils.responses import success_response, error_response




router= APIRouter()

@router.post("/login")
async def login_user(req:LoginRequest, db:AsyncDatabase = Depends(get_db) )->JSONResponse:

    try:
        user= await get_user_by_email(db, req.email)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) 
        password_check= verify_password(req.password, user.password_hash)
        if not password_check:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    except HTTPException:
        res= error_response(status= status.HTTP_401_UNAUTHORIZED, message="Login Failed. Incorrect Email or Password")
        response= JSONResponse(content=res.model_dump(mode="json"), status_code= status.HTTP_401_UNAUTHORIZED)
        return  response
    
    
    user_id= str(user.id)
    token= create_access_token(user_id)
    data= TokenResponse(access_token= token)
    res= success_response(data= data,
                          status= status.HTTP_200_OK)
    response= JSONResponse(
        content= res.model_dump(mode="json"),
        status_code= res.status
    )
    
    return response