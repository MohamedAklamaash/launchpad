"""ECS nginx sidecar command — real-AWS incident (e2e-web): `nginx -t` failed
('server_names_hash_bucket_size') but the sidecar's shell command backgrounded the whole
test-then-start chain (`nginx -t && nginx -g "daemon off;" &`), so the failure never
reached the foreground script's exit code — ECS reported a clean exit 0 for a task that
never served anything. See test_container_config.py for the config-content half of the
same incident (server_names_hash_bucket_size).

These are black-box checks of the actual command's exit-code contract, run inside the
exact nginx image the sidecar runs (aws.ecs.NGINX_SIDECAR_IMAGE), not of its literal
text — a passing test here proves the fix, not just that the string changed.
"""
import base64
import shutil
import subprocess
import uuid
from unittest.mock import MagicMock

import pytest
from aws.ecs import NGINX_SIDECAR_IMAGE, ECSClient


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="docker not available")


@pytest.fixture(scope="module", autouse=True)
def _nginx_image_pulled():
    """Pull once up front so a registry problem (public ECR rate-limits CI runners:
    "toomanyrequests: Data limit exceeded") skips these tests instead of reading as a
    sidecar behaviour failure."""
    pull = subprocess.run(["docker", "pull", "-q", NGINX_SIDECAR_IMAGE], capture_output=True, text=True,
                          timeout=120, check=False)
    if pull.returncode != 0:
        pytest.skip(f"could not pull {NGINX_SIDECAR_IMAGE}: {pull.stderr.strip()[:200]}")

BROKEN_CONF = "events {} http { this is not valid nginx config {{{ }"


def _nginx_sidecar_shell(container_port: int) -> str:
    """The exact shell script ECSClient.create_task_definition ships as the nginx
    container's command — not a hand-copy, extracted the same way
    test_host_mode_deploy_wiring.py does for the same container definition."""
    ecs = ECSClient.__new__(ECSClient)
    ecs.client = MagicMock()
    ecs.client.meta.region_name = "us-east-1"
    ecs.client.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": "arn:x"}
    }
    ecs.create_task_definition(
        family="myapp", image="img:latest", cpu=0.25, memory=0.5, envs={},
        execution_role_arn="arn:x", container_port=container_port, app_name="myapp",
    )
    container_defs = ecs.client.register_task_definition.call_args.kwargs["containerDefinitions"]
    nginx_container = next(c for c in container_defs if c["name"].endswith("-nginx"))
    return nginx_container["command"][-1]


def test_nginx_sidecar_exits_nonzero_when_nginx_t_fails():
    shell = _nginx_sidecar_shell(container_port=8000)
    b64 = base64.b64encode(BROKEN_CONF.encode()).decode()
    script = f"NGINX_CONFIG_B64='{b64}'\n{shell}\n"

    result = subprocess.run(
        ["docker", "run", "--rm", NGINX_SIDECAR_IMAGE, "/bin/sh", "-c", script],
        capture_output=True, text=True, timeout=20, check=False,
    )

    assert result.returncode != 0, (
        f"sidecar exited 0 on a broken nginx config — stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "nginx config test failed" in result.stdout


def test_nginx_sidecar_keeps_running_when_nginx_t_passes():
    """Regression guard: the fix above must not change behavior for a valid config —
    nginx starts, the sidecar finds the app port, and keeps running (it does not exit)."""
    container_port = 9000
    shell = _nginx_sidecar_shell(container_port=container_port)
    # Fake the app with a second real listener nginx itself opens on the "app" port —
    # a busybox `nc -l` accepts exactly one connection and exits, and the sidecar's own
    # `nc -z` probes are themselves connections, so a one-shot (or naively respawned)
    # nc listener races the sidecar's own health checks and is flaky. A real persistent
    # listener has no such race.
    app_stand_in_conf = (
        "events {} http { "
        "server { listen 80; location / { return 200; } } "
        f"server {{ listen {container_port}; location / {{ return 200; }} }} "
        "}"
    )
    b64 = base64.b64encode(app_stand_in_conf.encode()).decode()
    script = f"NGINX_CONFIG_B64='{b64}'\n{shell}\n"
    name = f"nginx-sidecar-test-{uuid.uuid4().hex[:8]}"

    proc = subprocess.Popen(
        ["docker", "run", "--rm", "--name", name, NGINX_SIDECAR_IMAGE, "/bin/sh", "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        proc.wait(timeout=6)
        pytest.fail(
            f"sidecar exited early with rc={proc.returncode}: {proc.stdout.read()}"
        )
    except subprocess.TimeoutExpired:
        pass  # still running after 6s, as expected — nginx never exited
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
