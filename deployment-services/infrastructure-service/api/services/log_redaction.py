"""Allowlist redaction for text derived from terraform stdout/stderr.

Drop-by-default: a line survives only by matching a shape this module knows to be
safe (worker markers, resource lifecycle lines, plan/apply summaries, diagnostic blocks
with credentials scrubbed, boto3 ClientError reduced to code + operation). Everything
else collapses to `… (N lines withheld)`.

Denylisting was rejected in the audit: terraform echoes provider config, plan diffs and
`terraform output -json` bodies, and there is no closed list of what a secret looks like
in those. This module is a fixed point — redacting its own output changes nothing,
counts included — so a value can be re-redacted at every write site without drift.
`clip_head` / `clip_tail` truncate on boundaries that preserve that property.

Stdlib only; no Django. Operates on `-no-color` output.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

REDACTED = "<redacted>"
WITHHELD_DIAGNOSTIC = "Error: <diagnostic withheld: contained a credential>"
MAX_DIAGNOSTIC_CHARS = 16_000

_WITHHELD_MARKER = re.compile(r"^… \((\d+) lines withheld\)$")
_STRUCTURAL = re.compile(r"^[╷│╵\s]*$")

# Platform-composed templates, anchored at line start. Only the template itself is
# kept; whatever was interpolated after it is re-classified on its own.
#
# Three shapes keep the whole line: `[OUTPUT] parsed keys: .*`, `[MOCK.*` and the
# `_SUMMARY` lines below. That is safe only because their tails are composed by the
# worker or by terraform itself, never by a customer — a template whose tail could
# carry customer-supplied text must stop at the template.
_MARKER = re.compile(
    r"^(?:"
    r"\[INIT\]|\[COMMAND\]|\[ERROR\]|\[FAILED UPDATE\]|\[DESTROY\]"
    r"|\[OUTPUT\] parsed keys: .*|\[OUTPUT FETCH FAILED\]|\[MOCK.*"
    r"|Retry \d+:|Update failed(?:; environment restored to ACTIVE)?:"
    r"|Cleanup:(?: All resources were destroyed\.)?"
    r"|WARNING: Cleanup failed\. Manual cleanup required in AWS account\."
    r"|Destroy failed:|Destroy blocked: \d+ database\(s\) must be deleted first"
    r"|Apply succeeded but reading outputs failed:|Terraform execution failed:"
    r"|Missing AWS credentials for terraform execution"
    r"|(?:PROVISIONING|UPDATING|DESTROYING) (?:update could not be recovered after \d+ attempts;"
    r" environment returned to ACTIVE|abandoned after \d+ recovery attempts)"
    r"|Refusing (?:to provision a mock infrastructure outside dev mode"
    r"|mock provisioning against a real infrastructure"
    r"|real AssumeRole against a mock infrastructure|mock AssumeRole against a real infrastructure)"
    r"|Invalid (?:aws_region|vpc_cidr|database name|cloud provider)"
    r"|AWS Account ID is required in the infrastructure code field"
    r"|" + re.escape(WITHHELD_DIAGNOSTIC) + r"(?: \(\w+\))?"
    r")"
)

_LIFECYCLE = re.compile(
    r'^(?P<addr>[\w"\[\]-]+(?:\.[\w"\[\]-]+)+): '
    r"(?P<verb>Creating|Creation complete|Modifying|Modifications complete|Destroying"
    r"|Destruction complete|Still creating|Still destroying|Still modifying|Refreshing state"
    r"|Reading|Read complete)"
    r"(?P<after>\.\.\.| after \d+[\dhms]*)"
    r"(?P<elapsed> \[\d+[\dhms]* elapsed\])?"
    r"(?P<id> \[id=[^\]]*\])?\s*$"
)
_SUMMARY = re.compile(r"^(?:Plan: |Apply complete! |Destroy complete! |No changes\. |Outputs:$)")
_BLOCK_START = re.compile(r"^[╷│ ]*(?:Error|Warning): ")
_CLIENT_ERROR = re.compile(r"An error occurred \((\w+)\) when calling the (\w+) operation")
_CLIENT_ERROR_TO_EOL = re.compile(_CLIENT_ERROR.pattern + r".*$")
_CLIENT_ERROR_REDUCED = r"An error occurred (\1) when calling the \2 operation"

_BLOCK_SCRUBS = (
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    # Third-line defence for the ElastiCache auth token (random_password length=32,
    # special=false — see test_elasticache_token_contract). `_` is deliberately not a
    # boundary: `\b` would match the tail of a longer identifier.
    re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9]{32}(?![A-Za-z0-9_])"),
    re.compile(r"arn:aws:iam::\d{12}:user/\S+"),
    re.compile(r"/dev/shm/tf-\S+"),
)
_WRAPPED_CHECKS = (*_BLOCK_SCRUBS, _CLIENT_ERROR)
_WITHHELD_RESOURCE_WORDS = re.compile(r"elasticache|secretsmanager|random_password", re.IGNORECASE)
_ERROR_CODE = re.compile(
    r"An error occurred \((\w+)\)"
    r"|api error (\w+):"
    r"|\b([A-Z][A-Za-z]+?(?:Exception|Fault|Error|Denied|Value|Combination|Found|Exists"
    r"|Exceeded|Unavailable|Operation|InUse|State|Capacity|Violation|Throttling|Conflict))(?=:)"
)
_SEAM = re.compile(r"\n[ \t]*[╷│╵]?[ \t]*")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*[╷│╵]?[ \t]*(?=\n|$)")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    kept_lines: int
    withheld_lines: int


def scrub_exact_values(text: str, values: Iterable[str]) -> str:
    for value in values:
        if value:
            text = text.replace(value, REDACTED)
    return text


def redact_provisioning_text(text: str | None, *, secrets: Iterable[str] = ()) -> RedactionResult:
    """`secrets` are exact values known to the caller (the STS credentials terraform ran
    with). They are scrubbed inline and also checked against each diagnostic block's
    wrapped form, which a plain `str.replace` cannot see."""
    secrets = tuple(s for s in secrets if s)
    if not text:
        return RedactionResult("", 0, 0)
    return _Redactor(secrets).run(scrub_exact_values(text, secrets))


def clip_tail(text: str, limit: int) -> str:
    """At most `limit` chars from the end of redacted text, starting on a line that
    classifies on its own, so the result is still a fixed point of the redactor.

    A cut that lands mid-line or inside a diagnostic block is not: the orphaned head
    is re-classified and withheld on the next write, and a drift check comparing stored
    text to its re-redaction would fire on every truncated row forever."""
    if len(text) <= limit:
        return text
    lines = text[-limit:].split("\n")
    first_complete = 0 if text[-limit - 1] == "\n" else 1
    start = next((k for k in range(first_complete, len(lines)) if not _continues_block(lines[k])), len(lines))
    return "\n".join(lines[start:])


def clip_head(text: str, limit: int) -> str:
    """At most `limit` chars from the start of redacted text, on a line boundary. Only
    the trailing partial line is dropped; every earlier line keeps the head that
    classifies it. A single line longer than `limit` is cut as-is — the only kept shape
    that long is a diagnostic, and a truncated `Error:` line is still one."""
    if len(text) <= limit:
        return text
    end = text.rfind("\n", 0, limit + 1)
    return text[:end] if end != -1 else text[:limit]


def _dewrap(text: str) -> str:
    """Join continuation lines so a value terraform wrapped at 80 columns is whole again.
    A blank continuation line is a paragraph break, not a wrap seam: it stays, so the
    paragraphs either side of it cannot glue into one word and hide a token from the
    boundary assertions in `_BLOCK_SCRUBS`."""
    return "\n".join(_SEAM.sub("", paragraph) for paragraph in _PARAGRAPH_BREAK.split(text))


class _Redactor:
    def __init__(self, secrets: tuple[str, ...]):
        self._secrets = secrets
        self._out: list[str] = []
        self._kept = 0
        self._withheld = 0
        self._pending = 0

    def run(self, text: str) -> RedactionResult:
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            i = self._step(lines, i)
        self._flush()
        return RedactionResult("\n".join(self._out), self._kept, self._withheld)

    def _step(self, lines: list[str], i: int) -> int:
        line = lines[i]
        if _STRUCTURAL.match(line):
            return i + 1
        if withheld := _WITHHELD_MARKER.match(line):
            self._pending += int(withheld.group(1))
            return i + 1
        if marker := _MARKER.match(line):
            self._keep(marker.group(0).rstrip())
            tail = line[marker.end():].strip()
            if not tail:
                return i + 1
            lines[i] = tail
            return i
        if lifecycle := _LIFECYCLE.match(line):
            self._keep(_rebuild_lifecycle(lifecycle))
            return i + 1
        if _SUMMARY.match(line):
            self._keep(line)
            return i + 1
        if _BLOCK_START.match(line):
            end = _block_end(lines, i)
            for rendered in self._render_block(lines[i:end]):
                self._keep(rendered)
            return end
        if client_error := _CLIENT_ERROR.search(line):
            self._keep(client_error.expand(_CLIENT_ERROR_REDUCED))
            return i + 1
        self._pending += 1
        return i + 1

    def _keep(self, line: str) -> None:
        self._flush()
        self._out.append(line)
        self._kept += 1

    def _flush(self) -> None:
        if self._pending:
            self._out.append(f"… ({self._pending} lines withheld)")
            self._withheld += self._pending
            self._pending = 0

    def _render_block(self, block: list[str]) -> list[str]:
        text = "\n".join(block)
        if len(text) > MAX_DIAGNOSTIC_CHARS or self._withhold_whole(block):
            return [_withheld_marker(text)]
        return [_scrub_block_line(line) for line in block]

    def _withhold_whole(self, block: list[str]) -> bool:
        """Fail closed: after the per-line scrub has taken everything it can see, the
        block is dewrapped and any pattern that still matches must have been assembled
        across a wrap seam — the one place a per-line scrub is blind."""
        dewrapped = _dewrap("\n".join(block))
        if _WITHHELD_RESOURCE_WORDS.search(dewrapped):
            return True
        if any(secret in dewrapped for secret in self._secrets):
            return True
        neutralised = _dewrap("\n".join(_neutralise_line(line) for line in block))
        return any(pattern.search(neutralised) for pattern in _WRAPPED_CHECKS)


def _rebuild_lifecycle(match: re.Match) -> str:
    masked_id = " [id=…]" if match.group("id") else ""
    return (f"{match.group('addr')}: {match.group('verb')}{match.group('after')}"
            f"{match.group('elapsed') or ''}{masked_id}")


def _block_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines) and "╵" not in lines[end - 1] and _continues_block(lines[end]):
        end += 1
    return end


# A block ends at a blank line or `╵` in terraform's own output; it must also end at
# every shape `_step` keeps, because the blank terminator does not survive a first pass
# and a second pass would otherwise swallow the lines that follow the block.
_BLOCK_TERMINATORS = (_WITHHELD_MARKER, _MARKER, _LIFECYCLE, _SUMMARY, _BLOCK_START, _CLIENT_ERROR)


def _continues_block(line: str) -> bool:
    return bool(line.strip()) and not any(pattern.match(line) for pattern in _BLOCK_TERMINATORS)


def _withheld_marker(text: str) -> str:
    """The bare marker plus the AWS error code when one is safely extractable. A withheld
    ElastiCache failure is usually a node type or subnet group problem, and the code is
    the customer's only lead."""
    match = _ERROR_CODE.search(text)
    if not match:
        return WITHHELD_DIAGNOSTIC
    code = next(group for group in match.groups() if group)
    if any(pattern.search(code) for pattern in _BLOCK_SCRUBS):
        return WITHHELD_DIAGNOSTIC
    return f"{WITHHELD_DIAGNOSTIC} ({code})"


def _scrub_block_line(line: str) -> str:
    line = _CLIENT_ERROR_TO_EOL.sub(_CLIENT_ERROR_REDUCED, line)
    for pattern in _BLOCK_SCRUBS:
        line = pattern.sub(REDACTED, line)
    return line


def _neutralise_line(line: str) -> str:
    """Everything the per-line pass can see, removed outright rather than reduced, so a
    ClientError the wrapped check still finds is one that only exists across a seam."""
    return _CLIENT_ERROR_TO_EOL.sub("", _scrub_block_line(line))
