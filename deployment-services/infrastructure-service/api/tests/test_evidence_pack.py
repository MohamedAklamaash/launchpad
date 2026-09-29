"""Compliance evidence pack (F5): the honest-limitations invariants (uninstantiated
modules absent from the applied list and present in the limitations text), rendered
policy equals the committed one per compute_type, mock-infra drift handling, the
owner-only authz ladder, and the per-user rate budget."""
import json
import re
import shutil
import uuid
import zipfile
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from api.cloud_providers.aws.iam_policy import policy_data
from api.services.evidence_pack import (
    _account_wide_actions,
    _compute_type_modules,
    _drift_summary_markdown,
    all_terraform_modules,
    build_evidence_pack,
    instantiated_terraform_modules,
)
from api.services.terraform_worker import TerraformWorker
from rest_framework.test import APIRequestFactory, force_authenticate

EXPECTED_NEVER_INSTANTIATED = {"security", "secrets", "cloud_optimizer"}

_RENDERED_MODULE_RE = re.compile(r'module\s+"(\w+)"\s*\{')


# ── module derivation ─────────────────────────────────────────────────────────────

def test_never_instantiated_modules_absent_from_instantiated_list():
    instantiated = instantiated_terraform_modules()
    assert instantiated & EXPECTED_NEVER_INSTANTIATED == set()
    assert {"vpc", "iam", "ecs", "alb", "ecr", "eks"} <= instantiated


def test_never_instantiated_modules_present_on_disk():
    excluded = all_terraform_modules() - instantiated_terraform_modules()
    assert excluded == EXPECTED_NEVER_INSTANTIATED


def test_ecs_module_derivation_matches_what_the_generator_actually_renders(db):
    """The regex-over-source derivation is fragile to conditional emission or helper
    indirection — pin it against the real rendered output so a future change to
    `_generate_config_ecs` that the regex can't see is caught here, not silently."""
    rendered = TerraformWorker._generate_config_ecs({}, str(uuid.uuid4()), "bucket", "table", "us-east-1")
    assert set(_RENDERED_MODULE_RE.findall(rendered)) == _compute_type_modules("ecs_fargate")


def test_eks_module_derivation_matches_what_the_generator_actually_renders(db, settings):
    settings.EKS_PUBLIC_ACCESS_CIDRS = ["203.0.113.0/24"]
    rendered = TerraformWorker._generate_config_eks(
        {}, str(uuid.uuid4()), "bucket", "table", "us-east-1", "123456789012"
    )
    assert set(_RENDERED_MODULE_RE.findall(rendered)) == _compute_type_modules("eks")


# ── account-wide actions (R3) ────────────────────────────────────────────────────

def test_account_wide_actions_list_more_than_just_iam_star():
    """The limitations section must not read as though only iam:* is unscoped — every
    Allow action with Resource "*" is account-wide, not just the IAM one."""
    doc = policy_data.document("ecs_fargate")
    actions = _account_wide_actions(doc)
    assert "iam:*" in actions
    assert "s3:*" in actions
    assert "secretsmanager:*" in actions
    assert len(actions) > 1


# ── drift markdown rendering (past the mock-only branch) ────────────────────────────
# build_evidence_pack's own tests all use is_mock=True, so _drift_summary_markdown's
# non-mock wording (R2's "wider access", R1's other_policies line, extra_statements) has
# no coverage there — exercise it directly against crafted drift dicts.

def _identical_policy():
    return {"available": True, "identical": True, "missing_allows": [], "missing_denies": [], "extra_allows": [], "extra_denies": [], "other_policies": None}


def _identical_trust():
    return {"available": True, "identical": True, "principal_matches": True, "external_id_present": True, "external_id_matches": True, "extra_statements": []}


def test_missing_deny_or_extra_allow_reads_as_wider_access():
    drift = {
        "note": None,
        "policy": {**_identical_policy(), "identical": False, "missing_denies": [{"effect": "Deny"}]},
        "trust_policy": _identical_trust(),
    }
    assert "wider access" in _drift_summary_markdown(drift)


def test_other_policies_renders_even_when_policy_unavailable():
    """R1/bug-fix (b): an AdministratorAccess attachment found alongside a detached
    LaunchpadDeploymentPolicy must not be buried inside an 'unavailable' line — it has to
    surface in the prose an auditor actually reads."""
    drift = {
        "note": None,
        "policy": {
            "available": False,
            "reason": "policy_not_attached",
            "missing_allows": [], "missing_denies": [], "extra_allows": [], "extra_denies": [],
            "other_policies": {"attached": ["arn:aws:iam::aws:policy/AdministratorAccess"], "inline": []},
        },
        "trust_policy": _identical_trust(),
    }
    summary = _drift_summary_markdown(drift)
    assert "unavailable" in summary
    assert "other attached polic" in summary


def test_extra_trust_statements_are_called_out_in_the_summary():
    drift = {
        "note": None,
        "policy": _identical_policy(),
        "trust_policy": {**_identical_trust(), "identical": False, "extra_statements": [{"Principal": "*"}]},
    }
    assert "additional statement" in _drift_summary_markdown(drift)


def test_sibling_external_ids_are_reported_without_being_treated_as_drift():
    """A second Launchpad infrastructure sharing this AWS account's role adds its
    ExternalId to the same trust statement (create_aws_role.sh's any-of merge) — this
    must read as an informational count, not as drift, when the trust policy is
    otherwise identical."""
    drift = {
        "note": None,
        "policy": _identical_policy(),
        "trust_policy": {**_identical_trust(), "sibling_external_ids": 1},
    }
    summary = _drift_summary_markdown(drift)
    assert "matches the expected shape" in summary
    assert "other ExternalId" in summary
    assert "not counted as drift" in summary


@pytest.fixture(autouse=True)
def _stub_infra_queue(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("api.services.database_service.InfraQueue", fake)
    monkeypatch.setattr("api.services.infrastructure.InfraQueue", fake)
    return fake


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, compute_type="ecs_fargate", is_mock=True, policy_version=None):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", compute_type=compute_type,
            max_cpu=1024, max_memory=512, code="123456789012", metadata={}, policy_version=policy_version,
        )
        if is_mock:
            # is_mock is editable=False; set directly and persist (matches
            # test_authenticate_dev_mode.py's convention).
            Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
            infra.refresh_from_db()
        return owner, infra
    return _make


# ── pack contents ─────────────────────────────────────────────────────────────────

def _namelist(zip_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return zf.namelist()


def _read(zip_bytes: bytes, name: str) -> str:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return zf.read(name).decode()


def test_pack_contains_expected_files_with_no_absolute_or_traversal_paths(make_infra):
    _owner, infra = make_infra()
    zip_bytes = build_evidence_pack(infra)

    names = _namelist(zip_bytes)
    assert set(names) == {"EVIDENCE.md", "manifest.json", "policy.expected.json", "trust-policy.expected.json", "drift.json"}
    for name in names:
        assert not name.startswith("/")
        assert ".." not in name


@pytest.mark.parametrize("compute_type", ["ecs_fargate", "eks"])
def test_rendered_policy_matches_policy_data_document(make_infra, compute_type):
    _owner, infra = make_infra(compute_type=compute_type)
    zip_bytes = build_evidence_pack(infra)

    rendered = json.loads(_read(zip_bytes, "policy.expected.json"))
    assert rendered == policy_data.document(compute_type)


def test_mock_infra_drift_section_says_unavailable(make_infra):
    _owner, infra = make_infra(is_mock=True)
    zip_bytes = build_evidence_pack(infra)

    drift = json.loads(_read(zip_bytes, "drift.json"))
    assert drift["policy"]["available"] is False
    assert drift["trust_policy"]["available"] is False
    assert "mock infrastructure" in drift["note"]
    assert "mock infrastructure" in _read(zip_bytes, "EVIDENCE.md")


def test_limitations_list_excluded_modules_and_omit_instantiated_ones(make_infra):
    _owner, infra = make_infra(compute_type="ecs_fargate")
    zip_bytes = build_evidence_pack(infra)

    evidence = _read(zip_bytes, "EVIDENCE.md")
    assert "## Honest limitations" in evidence
    for module in EXPECTED_NEVER_INSTANTIATED:
        assert f"`{module}`" in evidence
    manifest = json.loads(_read(zip_bytes, "manifest.json"))
    modules = manifest["terraform_modules"]
    assert set(modules["never_instantiated"]) == EXPECTED_NEVER_INSTANTIATED
    assert EXPECTED_NEVER_INSTANTIATED.isdisjoint(modules["applied_for_compute_type"])
    assert EXPECTED_NEVER_INSTANTIATED.isdisjoint(modules["applied_per_managed_database"])
    assert "`s3:*`" in evidence
    assert "`secretsmanager:*`" in evidence


def test_ecs_infra_does_not_claim_eks_module_and_vice_versa(make_infra):
    _owner, ecs_infra = make_infra(compute_type="ecs_fargate")
    ecs_manifest = json.loads(_read(build_evidence_pack(ecs_infra), "manifest.json"))
    assert "eks" not in ecs_manifest["terraform_modules"]["applied_for_compute_type"]

    _owner, eks_infra = make_infra(compute_type="eks")
    eks_manifest = json.loads(_read(build_evidence_pack(eks_infra), "manifest.json"))
    assert "eks" in eks_manifest["terraform_modules"]["applied_for_compute_type"]
    assert "ecs" not in eks_manifest["terraform_modules"]["applied_for_compute_type"]


def test_excluded_modules_text_is_driven_by_the_derived_list_not_hardcoded(make_infra, monkeypatch):
    """A module the derivation doesn't know a purpose for must still be named — proving
    the prose is keyed on `all_terraform_modules() - instantiated_terraform_modules()`
    rather than just happening to mention the three real module names."""
    monkeypatch.setattr(
        "api.services.evidence_pack.all_terraform_modules",
        lambda: instantiated_terraform_modules() | {"totally_fake_module"},
    )
    _owner, infra = make_infra()
    evidence = _read(build_evidence_pack(infra), "EVIDENCE.md")
    assert "`totally_fake_module`" in evidence
    assert "purpose not documented" in evidence


def test_iam_star_limitation_is_eks_specific_when_compute_type_is_eks(make_infra):
    _owner, infra = make_infra(compute_type="eks")
    evidence = _read(build_evidence_pack(infra), "EVIDENCE.md")
    assert "defense in depth" in evidence
    assert "EKS scoping" in evidence


def test_iam_star_limitation_is_generic_for_ecs_fargate(make_infra):
    _owner, infra = make_infra(compute_type="ecs_fargate")
    evidence = _read(build_evidence_pack(infra), "EVIDENCE.md")
    assert "account-wide" in evidence
    assert "EKS scoping" not in evidence


# ── ce: caveat is conditional on the data, never hard-coded ────────────────────────

@pytest.fixture
def policy_sandbox(tmp_path, monkeypatch):
    policy_path = tmp_path / "policy.json"
    shutil.copy(policy_data.POLICY_PATH, policy_path)
    monkeypatch.setattr(policy_data, "POLICY_PATH", policy_path)
    policy_data.load.cache_clear()
    yield policy_path
    policy_data.load.cache_clear()


def _write_policy(path, raw: dict):
    path.write_text(json.dumps(raw))
    policy_data.load.cache_clear()


def _without_ce_actions(statements: list[dict]) -> list[dict]:
    """Drop any ce: action from every statement, dropping statements left with none.
    `policy.json` on main carries a ce: grant since F4 (v3) — copying it verbatim is no
    longer a "no ce action" baseline, so this constructs one explicitly instead."""
    stripped = []
    for statement in statements:
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else actions
        kept = [a for a in actions if not a.lower().startswith("ce:")]
        if kept:
            stripped.append({**statement, "Action": kept})
    return stripped


def test_ce_caveat_absent_when_no_ce_action_in_policy(make_infra, policy_sandbox):
    raw = json.loads(policy_data.POLICY_PATH.read_text())
    raw["statements"] = _without_ce_actions(raw["statements"])
    # The evidence pack also echoes every policy.json note verbatim regardless of
    # whether the grant it describes is still present (true for every note, not just
    # ce: — narrowing statements alone never scrubs documentation). A policy that
    # genuinely has no ce: action wouldn't carry a note explaining one either.
    raw["notes"] = [n for n in raw["notes"] if "cost explorer" not in n.lower() and not n.strip().startswith("ce:")]
    _write_policy(policy_sandbox, raw)
    _owner, infra = make_infra()

    evidence = _read(build_evidence_pack(infra), "EVIDENCE.md")
    assert "Cost Explorer" not in evidence


def test_ce_caveat_present_when_policy_grants_a_ce_action(make_infra, policy_sandbox):
    raw = json.loads(policy_data.POLICY_PATH.read_text())
    raw["statements"].append({"Effect": "Allow", "Action": "ce:GetCostAndUsage", "Resource": "*"})
    _write_policy(policy_sandbox, raw)
    _owner, infra = make_infra()

    evidence = _read(build_evidence_pack(infra), "EVIDENCE.md")
    assert "Cost Explorer" in evidence


# ── view: authz ladder + rate budget ────────────────────────────────────────────────

def _get_pack(factory, user, infra_id):
    from api.views.evidence_pack import evidence_pack

    request = factory.get(f"/api/v1/infrastructures/{infra_id}/evidence-pack/")
    force_authenticate(request, user=user)
    return evidence_pack(request, infra_id=infra_id)


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """No fakeredis dependency in this codebase; mock the bound name like
    test_rate_budget.py and test_database_api.py do for their Redis-touching calls."""
    class _Pipe:
        def __init__(self, store):
            self.store, self.ops = store, []
        def incr(self, key):
            self.ops.append(("incr", key)); return self
        def ttl(self, key):
            self.ops.append(("ttl", key)); return self
        def execute(self):
            results = []
            for op, key in self.ops:
                if op == "incr":
                    self.store.counts[key] = self.store.counts.get(key, 0) + 1
                    results.append(self.store.counts[key])
                else:
                    results.append(self.store.expiry.get(key, -1))
            return results
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Redis:
        def __init__(self):
            self.counts, self.expiry = {}, {}
        def pipeline(self):
            return _Pipe(self)
        def expire(self, key, seconds):
            self.expiry[key] = seconds

    fake = _Redis()
    monkeypatch.setattr("shared.ratelimit.budget._redis", lambda: fake)
    return fake


def test_owner_gets_the_pack(factory, make_infra):
    owner, infra = make_infra()
    resp = _get_pack(factory, owner, str(infra.id))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/zip"
    assert str(infra.id) in resp["Content-Disposition"]


def test_invited_user_gets_403(factory, make_infra, make_user):
    _owner, infra = make_infra()
    invited = make_user()
    infra.invited_users.add(invited)

    resp = _get_pack(factory, invited, str(infra.id))
    assert resp.status_code == 403


def test_cross_tenant_stranger_gets_404(factory, make_infra, make_user):
    _owner, infra = make_infra()
    resp = _get_pack(factory, make_user(), str(infra.id))
    assert resp.status_code == 404


def test_malformed_infra_id_gets_404_not_500(factory, make_user):
    resp = _get_pack(factory, make_user(), "not-a-uuid")
    assert resp.status_code == 404


def test_budget_returns_429_once_exhausted(factory, make_infra, monkeypatch):
    # @rate_limited captures its limit/window args at import time, so overriding the
    # setting here wouldn't reach an already-decorated view — monkeypatch the budget
    # check itself instead, same as test_database_api.py does for the databases routes.
    monkeypatch.setattr("api.views.evidence_pack.EvidencePackService.get_pack", lambda *a, **k: (b"", None))
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: 42)
    owner, infra = make_infra()

    resp = _get_pack(factory, owner, str(infra.id))

    assert resp.status_code == 429
    assert resp["Retry-After"] == "42"
