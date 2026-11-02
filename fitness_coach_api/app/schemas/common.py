from pydantic import BaseModel , Field
from typing import TypeVar, Generic

DataType= TypeVar("DataType")


class Pagination(BaseModel):
    page : int = Field(
        ge= 1,
        description= "Current page"
    )
    page_size : int = Field(
        ge=1,
        description= "Number of items/rows in a single page"
    )
    total_items : int = Field(
        ge=0,
        description= "Total rows in the database"
    )
    total_pages : int= Field(
        ge=1,
        description= "Total pages"
    )
    has_next : bool = Field(
        description= "Check is there a page left(not fetched)"
    )
    has_previous: bool= Field(
        description= "Is there a page before the current one"
    )

class ApiResponse(BaseModel, Generic[DataType]):
    success : bool= Field(
        description= "True: if request is fulfiled and False: if request Fails"
    )
    
    status : int = Field(
        description= "HTTP status code"
    )
    
    message : str = Field(
        description= "General response message"
    )
    
    data : DataType = Field(
        description= "Data fetched as a result of the request"
    )
    
    pagination : Pagination | None = Field(
        default= None,
        description="For responses that require pagination(multiple fetches)"
    )