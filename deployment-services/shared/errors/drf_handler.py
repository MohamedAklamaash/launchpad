from rest_framework.views import exception_handler
from rest_framework.response import Response
from shared.errors.exception import HttpError

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if isinstance(exc, HttpError):
        data = {
            "message": exc.message,
        }
        if exc.details:
            data["details"] = exc.details
            
        return Response(data, status=exc.status_code)

    return response
