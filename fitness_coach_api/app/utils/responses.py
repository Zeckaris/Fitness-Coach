from app.schemas.common import DataType, Pagination, ApiResponse

def success_response(
    data:DataType,
    status:int,
    pagination: Pagination | None= None
    )->ApiResponse[DataType]:

    response= ApiResponse(
        status=status,
        success= True,
        data= data,
        message="Request processed successfully",
        pagination= pagination
    )
    return response

def error_response(
    status:int,
    message: str
)->ApiResponse[None]:
    response= ApiResponse(
        status= status,
        message=message,
        success= False,
        data= None,
        pagination= None
    )
    return response
