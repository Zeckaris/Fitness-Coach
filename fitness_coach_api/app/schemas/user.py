from pydantic import BaseModel, Field, ConfigDict
from bson import  ObjectId
from datetime import datetime

class UserCreate(BaseModel):
    email : str= Field(
        description= "Email input from user"
    )
    
    password : str = Field(
        min_length=8,
        max_length=32,
        description= "User password during registration"
    )


class UserResponse(BaseModel):
    id : str= Field(
        alias= "_id",
        description= "Unique user id"
    )
    email : str = Field(
        description="User Email"
    )
    
    created_at : datetime= Field(
        description= "User creation date"
    )
    
    last_login : datetime | None = Field(
        default=None,
        description= "Last time user logged in"
    )
    
    model_config = ConfigDict(
    arbitrary_types_allowed=True,
    populate_by_name=True
    )