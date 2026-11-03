import jwt
import os
from dotenv import load_dotenv
from datetime import timedelta,datetime, timezone

load_dotenv()
SECRET_KEY= os.getenv("JWT_SECRET_KEY")
ALGORITHM= os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES= int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)

if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY is not configured")

def create_access_token(user_id:str,
                        expires_delta: timedelta | None= None)->str:
    now= datetime.now(timezone.utc)
    expire= now + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload= {
        "sub":user_id,
        "iat": now,
        "exp":expire
    }
    
    token= jwt.encode(
        payload,
        SECRET_KEY,
        algorithm= ALGORITHM
    )
    
    return token

    



def decode_access_token(token : str)->dict:
    payload= jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM]
    )
    
    return payload