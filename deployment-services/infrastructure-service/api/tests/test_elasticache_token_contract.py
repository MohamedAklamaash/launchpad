"""The ElastiCache auth token is the one credential this platform writes into terraform
state, and the redactor's third-line defence for it is a 32-alphanumeric regex. Nothing
but a comment ties that regex to `random_password "auth_token"` in the elasticache
module — a silent drift there is a silent credential leak. Same idea as the policy.json
drift gate.
"""

import re

from api.services.log_redaction import (
    REDACTED,
    WITHHELD_DIAGNOSTIC,
    redact_provisioning_text,
)
from api.services.terraform_worker import TF_MODULES_DIR

ELASTICACHE_MAIN_TF = TF_MODULES_DIR / "modules" / "elasticache" / "main.tf"


def _auth_token_block():
    match = re.search(r'resource "random_password" "auth_token" \{(.*?)\n\}', ELASTICACHE_MAIN_TF.read_text(), re.DOTALL)
    assert match, "random_password.auth_token no longer exists in the elasticache module"
    return match.group(1)


def test_elasticache_auth_token_is_32_alphanumeric():
    body = _auth_token_block()
    assert re.search(r"^\s*length\s*=\s*32\s*$", body, re.MULTILINE), "auth_token length drifted from 32"
    assert re.search(r"^\s*special\s*=\s*false\s*$", body, re.MULTILINE), "auth_token now allows special chars"


TOKEN = "q8Zl2Xk9pR4sT7vW1yB3nM6cD0fG5hJa"


def test_redactor_scrubs_a_token_of_that_shape():
    out = redact_provisioning_text(f"Error: replication group rejected auth_token {TOKEN}\n").text
    assert TOKEN not in out
    assert REDACTED in out


def test_redactor_withholds_a_wrapped_token_of_that_shape():
    """Terraform wraps diagnostics at 80 columns, so the token usually reaches the log in
    two halves that the per-line regex cannot see. This is the path the contract exists
    to protect; the single-line case above never reaches it."""
    block = f"╷\n│ Error: replication group rejected auth_token {TOKEN[:16]}\n│ {TOKEN[16:]}\n╵\n"
    assert redact_provisioning_text(block).text == WITHHELD_DIAGNOSTIC
