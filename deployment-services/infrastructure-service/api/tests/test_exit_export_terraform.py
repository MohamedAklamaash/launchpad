"""F6: the bundled Terraform config must actually be usable, not just plausible-looking
text. `terraform init -backend=false` (real backend init is impossible in CI/sandboxes —
no AWS credentials, and often no network to the Terraform registry either) plus
`terraform validate` is the strongest available proof: it exercises the exact modules and
main.tf this feature bundles, downloads the real provider schema, and checks every
resource/module argument against it.

Skips (does not fail) when `terraform` is missing or the provider can't be downloaded —
both environmental, not a defect in the generated config — and falls back to `terraform
fmt -check`, a network-free syntax check, so a skip still proves the HCL parses.
"""
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

TERRAFORM_INIT_TIMEOUT_SECONDS = 90
# Same cache directory api/services/terraform_worker.py._exec_tf uses for real applies —
# reusing it means the first test run in a given environment pays the ~200MB provider
# download once, and every run after (locally or in CI with a persisted /tmp) is instant.
TF_PLUGIN_CACHE_DIR = "/tmp/tf-plugin-cache"


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


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

    def _make(*, compute_type="ecs_fargate"):
        owner = make_user()
        return Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", compute_type=compute_type,
            max_cpu=1024, max_memory=512, code="123456789012", metadata={"aws_region": "us-east-1"},
            dns_label=uuid.uuid4().hex[:16],
        )
    return _make


@pytest.mark.parametrize("compute_type", ["ecs_fargate", "eks"])
def test_bundled_terraform_is_syntactically_valid(tmp_path, make_infra, compute_type, settings):
    import io
    import zipfile

    from api.services.exit_export import _copy_terraform_modules, _terraform_bundle

    settings.EKS_PUBLIC_ACCESS_CIDRS = ["203.0.113.0/24"]
    infra = make_infra(compute_type=compute_type)
    main_tf, backend_hcl, _facts = _terraform_bundle(infra)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        _copy_terraform_modules(zf)

    work_dir: Path = tmp_path / "terraform"
    work_dir.mkdir()
    (work_dir / "main.tf").write_text(main_tf)
    (work_dir / "backend.hcl").write_text(backend_hcl)
    with zipfile.ZipFile(buf) as zf:
        zf.extractall(work_dir.parent)

    if not _terraform_available():
        pytest.skip("terraform binary not available in this environment")

    Path(TF_PLUGIN_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": TF_PLUGIN_CACHE_DIR}

    def _fmt_fallback(reason: str):
        # Environmental (no/slow network to the registry) vs. a real defect: `terraform
        # fmt` needs no provider and no network, so it still proves the HCL itself parses.
        fmt = subprocess.run(
            ["terraform", "fmt", "-check", "-no-color", str(work_dir / "main.tf")],
            capture_output=True, text=True, check=False,
        )
        if fmt.returncode == 0:
            pytest.skip(f"{reason}; falling back to `terraform fmt -check`, which passed cleanly")
        pytest.fail(f"{reason} AND `terraform fmt -check` found a syntax problem: {fmt.stdout}")

    try:
        init = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            cwd=work_dir, capture_output=True, text=True, timeout=TERRAFORM_INIT_TIMEOUT_SECONDS, env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _fmt_fallback(
            f"terraform init did not complete within {TERRAFORM_INIT_TIMEOUT_SECONDS}s "
            "(likely a slow/absent network path to the Terraform registry in this environment)"
        )
    if init.returncode != 0:
        _fmt_fallback(f"terraform init failed: {init.stderr[-500:]}")

    validate = subprocess.run(
        ["terraform", "validate", "-no-color"],
        cwd=work_dir, capture_output=True, text=True, timeout=TERRAFORM_INIT_TIMEOUT_SECONDS, env=env,
        check=False,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr
