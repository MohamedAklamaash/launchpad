from django.conf import settings
from django.http import JsonResponse

DEFAULT_HEADER_NAME = "X-INTERNAL-TOKEN"


class InternalAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

        self.expected_token = settings.INTERNAL_AUTH_TOKEN
        self.exempt_paths = getattr(
            settings,
            "INTERNAL_AUTH_EXEMPT_PATHS",
            []
        )

        header_name = getattr(
            settings,
            "INTERNAL_AUTH_HEADER_NAME",
            DEFAULT_HEADER_NAME
        )

        self.header_meta_key = (
            "HTTP_" + header_name.upper().replace("-", "_")
        )

    def __call__(self, request):
        path = request.path

        is_exempt = any(
            path == exempt or path.endswith(exempt)
            for exempt in self.exempt_paths
        )

        if not is_exempt:
            token = request.META.get(self.header_meta_key)

            if not token or token != self.expected_token:
                return JsonResponse(
                    {
                        "message": "Unauthorized in Internal middleware",
                        "details": "Internal token mismatch with expected token",
                    },
                    status=401,
                )

        response = self.get_response(request)
        return response
