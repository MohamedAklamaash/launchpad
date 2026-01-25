import logging
from django.http import JsonResponse
from shared.errors.exception import HttpError

logger = logging.getLogger(__name__)

class ErrorHandlerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except Exception as err:
            logger.exception("Unhandled error occurred", extra={"err": err})

            error = err if isinstance(err, HttpError) else None
            status_code = getattr(error, "status_code", 500)

            message = (
                "Internal Server Error"
                if status_code >= 500
                else getattr(error, "message", "Unknown Error")
            )

            payload = {"message": message}

            if error and error.details:
                payload["details"] = error.details

            return JsonResponse(payload, status=status_code)
