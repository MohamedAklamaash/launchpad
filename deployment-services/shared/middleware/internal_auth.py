import secrets

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
        # Prefix-based exemptions for parametric routes (e.g. webhook receivers
        # whose final path segment is a UUID we cannot enumerate at config time).
        self.exempt_prefixes = getattr(
            settings,
            "INTERNAL_AUTH_EXEMPT_PREFIXES",
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
            path == exempt or path == exempt.rstrip('/')
            for exempt in self.exempt_paths
        ) or any(
            # Require the match to end at a path boundary so a prefix like
            # "/api/v1/webhooks/" can't also exempt "/api/v1/webhooks-internal/...".
            path == prefix.rstrip('/') or path.startswith(prefix if prefix.endswith('/') else prefix + '/')
            for prefix in self.exempt_prefixes
        )

        if not is_exempt:
            token = request.META.get(self.header_meta_key)

            # Constant-time compare: a plain != is a timing oracle on the shared
            # service-to-service token (the entire S2S trust boundary).
            if not token or not secrets.compare_digest(token, self.expected_token):
                return JsonResponse(
                    {
                        "message": "Unauthorized in Internal middleware",
                        "details": "Internal token mismatch with expected token",
                    },
                    status=401,
                )

        response = self.get_response(request)
        return response
