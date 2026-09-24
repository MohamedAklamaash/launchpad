"""Make a deployment exception safe to store on a customer-visible field.

`Application.error_message` is written from the exception that failed a deploy and served
straight back over the API, so whatever the exception carries is disclosed. Two exception
types carry more than the customer asked for; everything else is a sentence this service
composed itself and is exactly what they need to read.

Deliberately not the terraform log redactor. That one is an allowlist because terraform
emits unbounded machine output with no closed list of what a secret looks like in it. An
exception message here is almost always worker-composed prose — "Application name 'my.app'
is not deployable on Kubernetes" — and drop-by-default would withhold the reason entirely,
leaving the customer a failed deploy and no explanation.
"""

import json
import re

MAX_ERROR_CHARS = 4_000

# The platform's own principal is an IAM *user* — it is the AssumeRole caller named in
# every customer's trust policy — and an AccessDenied renders it into the message. A
# customer's own principals are roles, so scrubbing user ARNs costs them no detail while
# removing the one identifier that is not theirs to see.
_IAM_USER_ARN = re.compile(r"arn:aws:iam::\d{12}:user/\S+")


def _client_error(exc) -> str:
    # Keep AWS's explanation: the code alone says an action failed, not which parameter
    # was wrong or which resource was missing.
    return _IAM_USER_ARN.sub("<redacted>", str(exc))


def _k8s_api_error(exc) -> str:
    """Status and the API's own message. `str(ApiException)` appends the full HTTP
    response headers and body, which is noise at best and echoes whatever the rejected
    request carried at worst."""
    status = getattr(exc, "status", "?")
    reason = getattr(exc, "reason", "") or ""
    message = ""
    body = getattr(exc, "body", None)
    if body:
        try:
            message = json.loads(body).get("message", "") or ""
        except (ValueError, AttributeError):
            message = ""
    detail = f": {message}" if message else ""
    return f"Kubernetes API error {status} ({reason}){detail}".strip()


def _is_k8s_api_error(exc) -> bool:
    # Imported lazily: an ECS-only deployment of this service need not have the client,
    # and if it is absent no exception here can be one of its types anyway.
    try:
        from kubernetes.client.rest import ApiException
    except ImportError:
        return False
    return isinstance(exc, ApiException)


def sanitize_deploy_error(exc: BaseException) -> str:
    from botocore.exceptions import ClientError

    if isinstance(exc, ClientError):
        text = _client_error(exc)
    elif _is_k8s_api_error(exc):
        text = _k8s_api_error(exc)
    else:
        text = str(exc)
    return text[:MAX_ERROR_CHARS]
