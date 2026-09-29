"""nginx sidecar config generation — F1b part 2 pre-review §5/§6 item 6.

Path mode (host_mode=False, the default) must stay byte-identical to what shipped before
F1b — GOLDEN_PATH_MODE is main's own output (verified byte-identical at write time), not a
hand-transcribed copy, so a stray character here fails the same way a real regression
would. Its one deliberate change since is `absolute_redirect off;` (the EKS sidecar-port
leak). Host mode drops the 301/rewrite/X-Forwarded-Prefix/ROOT_PATH machinery and adds the
dedicated health path in lockstep with aws/alb.py and the k8s readiness probe.
"""
import shutil
import subprocess

import pytest
from aws.container_config import (
    HOST_MODE_HEALTH_CHECK_PATH,
    generate_nginx_config,
    inject_routing_envs,
)
from aws.ecs import NGINX_SIDECAR_IMAGE

GOLDEN_PATH_MODE = '''
events {
    worker_connections 1024;
}
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    proxy_connect_timeout 60s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    client_max_body_size 100m;

    access_log /dev/stdout;
    error_log /dev/stderr info;

    upstream backend {
        server 127.0.0.1:8000 max_fails=3 fail_timeout=30s;
    }

    server {
        listen 80;

        # Relative Location on nginx's own redirects (/myapp -> /myapp/). An
        # absolute one bakes in nginx's own listen port and scheme: on EKS that's the
        # sidecar's unreachable port 18080, and behind a TLS ALB listener it downgrades
        # the client to http.
        absolute_redirect off;

        # ALB health check
        location = / {
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }

        # Redirect /app-name to /app-name/
        location = /myapp {
            return 301 /myapp/;
        }

        # Strip prefix and proxy to backend
        location /myapp/ {
            rewrite ^/myapp/(.*)$ /$1 break;
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Prefix /myapp;
            proxy_http_version 1.1;
            # WebSocket support
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_buffering off;
            # Return 503 (not 502) when backend is down so ALB marks target unhealthy
            proxy_next_upstream error timeout http_502 http_503;
            proxy_next_upstream_tries 1;
            proxy_intercept_errors on;
            error_page 502 503 504 =503 /healthz_down;
        }

        location = /healthz_down {
            internal;
            return 503 "service unavailable\n";
            add_header Content-Type text/plain;
        }
    }

    # WebSocket connection upgrade map
    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }
}
'''


def test_path_mode_nginx_config_is_byte_identical_to_golden():
    assert generate_nginx_config("myapp", 8000) == GOLDEN_PATH_MODE


def test_path_mode_redirects_are_relative_so_the_sidecar_port_never_leaks():
    """EKS e2e-kube incident: /e2e-kube 301'd to http://<alb>:18080/e2e-kube/, the sidecar's
    own listen port, which the ALB never exposes."""
    assert "absolute_redirect off;" in generate_nginx_config("myapp", 8000, listen_port=18080)


def test_path_mode_is_the_default():
    assert generate_nginx_config("myapp", 8000, listen_port=80) == generate_nginx_config(
        "myapp", 8000, listen_port=80, host_mode=False,
    )


def test_host_mode_requires_app_hostname():
    import pytest
    with pytest.raises(ValueError, match="app_hostname"):
        generate_nginx_config("myapp", 8000, host_mode=True)


def test_host_mode_drops_prefix_stripping_and_forwarded_prefix():
    config = generate_nginx_config("myapp", 8000, host_mode=True, app_hostname="myapp.abc123.launchpad.app")
    assert "X-Forwarded-Prefix" not in config
    assert "rewrite ^/myapp/" not in config
    assert "return 301 /myapp/;" not in config


def test_host_mode_forwards_proto_from_the_albs_header_not_scheme():
    config = generate_nginx_config("myapp", 8000, host_mode=True, app_hostname="myapp.abc123.launchpad.app")
    assert "X-Forwarded-Proto $http_x_forwarded_proto" in config
    assert "X-Forwarded-Proto $scheme" not in config


def test_host_mode_health_check_is_a_dedicated_path_not_root():
    config = generate_nginx_config("myapp", 8000, host_mode=True, app_hostname="myapp.abc123.launchpad.app")
    assert f"location = {HOST_MODE_HEALTH_CHECK_PATH} {{" in config
    assert "location = / {" not in config
    assert "location / {" in config  # root now proxies straight to the app


def test_host_mode_health_check_answers_regardless_of_which_server_block(app_hostname="myapp.abc123.launchpad.app"):
    """ALB target-group health checks may not carry the app's own Host header, so both
    the app's own server block and the default/legacy one must answer the health path."""
    config = generate_nginx_config("myapp", 8000, host_mode=True, app_hostname=app_hostname)
    assert config.count(f"location = {HOST_MODE_HEALTH_CHECK_PATH} {{") == 2


def test_host_mode_redirects_the_old_path_url_to_the_host_url():
    config = generate_nginx_config("myapp", 8000, host_mode=True, app_hostname="myapp.abc123.launchpad.app")
    assert "location ~ ^/myapp(/[^\\r\\n]*)?$ {" in config
    assert "return 301 https://myapp.abc123.launchpad.app$1;" in config


def test_host_mode_uses_two_server_blocks_dispatched_by_host_header():
    """The redirect location lives in a server block scoped to any Host other than the
    app's own — never in the block that actually proxies to the backend — so a real app
    route that happens to start with the app's own name (e.g. /api/users on an app named
    "api") can never be misrouted as an old path-mode artifact: it is only ever reached
    through the block matched by the app's own hostname, which has no such location at all."""
    config = generate_nginx_config("myapp", 8000, host_mode=True, app_hostname="myapp.abc123.launchpad.app")
    assert "server_name myapp.abc123.launchpad.app;" in config
    assert "server_name _;" in config
    assert "default_server" in config
    app_block, legacy_block = config.split("server_name myapp.abc123.launchpad.app;", 1)[1].split("server_name _;", 1)
    assert "return 301 https://" not in app_block
    assert "proxy_pass http://backend;" not in legacy_block


def test_host_mode_rejects_a_malicious_app_hostname():
    import pytest
    for bad_hostname in (
        "evil.com; provisioner {}",
        "a\nb",
        "myapp.abc123.launchpad.app\r\nX-Injected: 1",
        "",
        "no-dot-at-all",
        "-leading-hyphen.example.com",
    ):
        with pytest.raises(ValueError, match="app_hostname"):
            generate_nginx_config("myapp", 8000, host_mode=True, app_hostname=bad_hostname)


def test_host_mode_escapes_regex_metacharacters_in_app_name():
    """app_name is interpolated into a `location ~ regex` — a name containing regex
    metacharacters must not change what the pattern matches."""
    config = generate_nginx_config("my.app", 8000, host_mode=True, app_hostname="myapp.abc123.launchpad.app")
    assert r"^/my\.app(/[^\r\n]*)?$" in config


# ── inject_routing_envs ──────────────────────────────────────────────────────────────

def test_inject_routing_envs_path_mode_sets_root_path():
    envs = inject_routing_envs([], "myapp")
    by_name = {e["name"]: e["value"] for e in envs}
    assert by_name["ROOT_PATH"] == "/myapp"
    assert by_name["UVICORN_ROOT_PATH"] == "/myapp"
    assert by_name["FORWARDED_ALLOW_IPS"] == "*"


def test_inject_routing_envs_host_mode_drops_root_path():
    envs = inject_routing_envs([], "myapp", host_mode=True)
    names = {e["name"] for e in envs}
    assert "ROOT_PATH" not in names
    assert "UVICORN_ROOT_PATH" not in names
    by_name = {e["name"]: e["value"] for e in envs}
    assert by_name["FORWARDED_ALLOW_IPS"] == "*"


def test_inject_routing_envs_never_clobbers_a_user_set_host():
    envs = inject_routing_envs([{"name": "HOST", "value": "custom"}], "myapp", host_mode=True)
    by_name = {e["name"]: e["value"] for e in envs}
    assert by_name["HOST"] == "custom"


# ── server_names_hash_bucket_size (real-AWS incident: e2e-web) ──────────────────────────

def _max_length_host_mode_hostname() -> tuple[str, str]:
    """A realistic worst case for build_app_hostname (api/common/host_url.py): a 63-char
    DNS-label-shaped app slug, a 16-hex dns_label (secrets.token_hex(8)), and a real
    platform base domain — the exact shape that produced
    `e2e-web.1e922243bb3654a8.launchpad.aklamaash.me` on real AWS, just at the slug's
    length ceiling instead of a short one."""
    slug = "a" + "b" * 61 + "c"
    assert len(slug) == 63
    dns_label = "1e922243bb3654a8"
    base_domain = "launchpad.aklamaash.me"
    return slug, f"{slug}.{dns_label}.{base_domain}"


def test_host_mode_sets_a_large_enough_server_names_hash_bucket_size():
    """Default nginx bucket sizes (32/64 bytes) are too small for a 100+ byte server_name
    — nginx then fails `nginx -t` outright ('could not build server_names_hash'), the
    sidecar exits, and the task never comes up. See test_host_mode_nginx_config_is_valid_
    per_nginx_t below for the same check run through a real nginx binary."""
    slug, hostname = _max_length_host_mode_hostname()
    assert len(hostname) > 64
    config = generate_nginx_config(slug, 8000, host_mode=True, app_hostname=hostname)
    assert "server_names_hash_bucket_size 128;" in config


def test_path_mode_does_not_set_server_names_hash_bucket_size():
    assert "server_names_hash_bucket_size" not in generate_nginx_config("myapp", 8000)


def test_host_mode_nginx_config_is_valid_per_nginx_t(tmp_path):
    """Renders host mode with the realistic max-length hostname above and validates it
    with the actual nginx binary from the exact image the ECS sidecar runs
    (aws.ecs.NGINX_SIDECAR_IMAGE) via `docker run ... nginx -t`. Skipped when docker
    isn't available (e.g. CI has no docker-in-docker for this repo)."""
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
    except Exception:
        pytest.skip("docker daemon not reachable")

    # Pull separately so a registry problem (public ECR rate-limits CI runners:
    # "toomanyrequests: Data limit exceeded") skips instead of failing as if the
    # config were invalid. Only `nginx -t` itself decides pass/fail.
    pull = subprocess.run(["docker", "pull", "-q", NGINX_SIDECAR_IMAGE], capture_output=True, text=True,
                          timeout=120, check=False)
    if pull.returncode != 0:
        pytest.skip(f"could not pull {NGINX_SIDECAR_IMAGE}: {pull.stderr.strip()[:200]}")

    slug, hostname = _max_length_host_mode_hostname()
    config = generate_nginx_config(slug, 8000, host_mode=True, app_hostname=hostname)

    conf_path = tmp_path / "nginx.conf"
    conf_path.write_text(config)

    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{conf_path}:/etc/nginx/nginx.conf:ro",
            NGINX_SIDECAR_IMAGE, "nginx", "-t",
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
