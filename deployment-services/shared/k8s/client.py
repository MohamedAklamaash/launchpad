import base64
import logging
import os
import re
import tempfile
from contextlib import contextmanager

from kubernetes import client as kubernetes_client

from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

_SECRET_RE = re.compile(r"k8s-aws-v1\.[A-Za-z0-9_=-]+|X-Amz-Signature=[0-9a-fA-F]+")


class TokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_RE.sub("[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def install_redaction_filter() -> None:
    targets = [logging.getLogger(), logging.getLogger("kubernetes"), logging.getLogger("urllib3")]
    handlers = [h for t in targets for h in t.handlers]
    for sink in [*targets, *handlers]:
        if not any(isinstance(f, TokenRedactionFilter) for f in sink.filters):
            sink.addFilter(TokenRedactionFilter())


def _enforce_mock_real_gate(infrastructure, mode) -> None:
    dev_mode = is_dev_mode(mode)
    is_mock = bool(getattr(infrastructure, "is_mock", False))
    if is_mock and not dev_mode:
        raise ValueError("Refusing real k8s client against a mock infrastructure")
    if dev_mode and not is_mock:
        raise ValueError("Refusing mock k8s client against a real infrastructure")
    if is_mock:
        raise ValueError("Mock infrastructures have no real k8s client; use the mock k8s surface")


@contextmanager
def k8s_api_client(infrastructure, mode, *, endpoint: str, ca_data: str, token: str, token_provider=None):
    _enforce_mock_real_gate(infrastructure, mode)
    install_redaction_filter()

    ca_fd, ca_path = tempfile.mkstemp(prefix="eks-ca-", suffix=".crt")
    try:
        with os.fdopen(ca_fd, "wb") as ca_file:
            ca_file.write(base64.b64decode(ca_data))

        configuration = kubernetes_client.Configuration()
        configuration.host = endpoint
        configuration.ssl_ca_cert = ca_path
        configuration.api_key = {"authorization": token}
        configuration.api_key_prefix = {"authorization": "Bearer"}
        if token_provider is not None:
            # The 60s presign window (H5) outlives no polling loop; re-mint per request.
            configuration.refresh_api_key_hook = lambda config: config.api_key.update(
                {"authorization": token_provider()}
            )
        # Never raise this or log the Configuration object: its repr and
        # to_debug_report() both include api_key.
        configuration.debug = False

        api_client = kubernetes_client.ApiClient(configuration)
        try:
            yield api_client
        finally:
            api_client.close()
    finally:
        os.unlink(ca_path)
