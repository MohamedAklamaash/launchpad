import re

# Health check path in host mode (F1b, TLS activation) — nginx's `/` proxies straight to
# the app there (see below), so the canned health response path mode used at `/` needs a
# location no real app route could plausibly collide with. Mirrored at the per-app ALB
# target group (aws/alb.py) and the k8s readiness probe (api/k8s/deployer.py) — all three
# must move together or targets go unhealthy the moment a routing mode changes.
HOST_MODE_HEALTH_CHECK_PATH = "/_lp_health"

# A conservative DNS-label-shaped hostname: lowercase alnum/hyphen labels, at least two of
# them, no leading/trailing hyphen. app_hostname is interpolated directly into nginx
# config text (both as a `server_name` and inside a `return 301` target) — this is config
# injection surface, not just a cosmetic check, so anything outside this shape is refused
# rather than escaped.
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def _validate_app_hostname(app_hostname: str) -> None:
    if not app_hostname or not _HOSTNAME_RE.fullmatch(app_hostname):
        raise ValueError(f"invalid app_hostname for host mode: {app_hostname!r}")


def generate_nginx_config(app_name, backend_port, listen_port=80, host_mode=False, app_hostname=None):
    """NGINX config for one app's sidecar.

    Path mode (host_mode=False, the default): strips the /{app_name} prefix and injects
    X-Forwarded-Prefix, exactly as before F1b — byte-identical output, pinned by
    test_container_config.py's golden test. The ALB Ingress controller cannot rewrite
    paths (rewrite-target is nginx-ingress-only), so this sidecar is load-bearing on EKS as
    well as ECS.

    Host mode (F1b part 2): two server blocks dispatched by Host header, not one block
    dispatched by path. The app's own hostname (`server_name {app_hostname}`) gets the
    real serving block: `/` proxies straight through with no rewrite, no 301, no
    X-Forwarded-Prefix, and `/` is the app's own root rather than a reserved ALB health
    check target — see HOST_MODE_HEALTH_CHECK_PATH. `X-Forwarded-Proto` comes from the
    ALB's own `$http_x_forwarded_proto` rather than `$scheme`: nginx only ever sees plain
    HTTP from the ALB, so `$scheme` would report "http" even for a client's HTTPS request.
    Every other Host (`default_server`, the shared :80 path-based listener's Host header,
    and ALB health checks, which do not send the app's own Host) gets a second block that
    only 301-redirects the old `/{app_name}(/...)` path scheme to the host URL and 404s
    everything else — it never proxies to the backend, so it cannot be tricked into
    treating a real app route as a legacy path artifact just because the route happens to
    start with the app's own name (e.g. `/api/users` on an app named "api"): that
    ambiguity only existed when a single block tried to handle both cases by path alone.
    Health checks are answered in both blocks since ALB health check requests may not
    carry the app's own Host header. Host URLs are additive (see
    plan/F1b-tls-activation.md), not a migration — the old path URL keeps resolving.
    """
    if host_mode:
        _validate_app_hostname(app_hostname)
        escaped_app_name = re.escape(app_name)
        return f'''
events {{
    worker_connections 1024;
}}
http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # A host-mode server_name is up to a 63-char app slug plus a 16-hex dns_label plus
    # the platform base domain, well past nginx's default bucket size (32 or 64 bytes
    # depending on build). Too small a bucket fails config load outright ("could not
    # build server_names_hash"), which in ECS means nginx -t fails, the sidecar exits,
    # and the task never comes up — see the e2e-web incident this constant fixes.
    server_names_hash_bucket_size 128;

    proxy_connect_timeout 60s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    client_max_body_size 100m;

    access_log /dev/stdout;
    error_log /dev/stderr info;

    upstream backend {{
        server 127.0.0.1:{backend_port} max_fails=3 fail_timeout=30s;
    }}

    server {{
        listen {listen_port};
        server_name {app_hostname};

        location = {HOST_MODE_HEALTH_CHECK_PATH} {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}

        location / {{
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
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
        }}

        location = /healthz_down {{
            internal;
            return 503 "service unavailable\n";
            add_header Content-Type text/plain;
        }}
    }}

    # Any Host other than the app's own — the shared :80 path-based listener, an ALB
    # health check with no Host header, or anything else — lands here. This block never
    # proxies to the backend: it only redirects the legacy /{app_name} path scheme to the
    # host URL, so it can never misinterpret a real app route as an old path artifact.
    server {{
        listen {listen_port} default_server;
        server_name _;

        location = {HOST_MODE_HEALTH_CHECK_PATH} {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}

        location ~ ^/{escaped_app_name}(/[^\\r\\n]*)?$ {{
            return 301 https://{app_hostname}$1;
        }}

        location / {{
            return 404;
        }}
    }}

    # WebSocket connection upgrade map
    map $http_upgrade $connection_upgrade {{
        default upgrade;
        ''      close;
    }}
}}
'''
    return f'''
events {{
    worker_connections 1024;
}}
http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    proxy_connect_timeout 60s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    client_max_body_size 100m;

    access_log /dev/stdout;
    error_log /dev/stderr info;

    upstream backend {{
        server 127.0.0.1:{backend_port} max_fails=3 fail_timeout=30s;
    }}

    server {{
        listen {listen_port};

        # ALB health check
        location = / {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}

        # Redirect /app-name to /app-name/
        location = /{app_name} {{
            return 301 /{app_name}/;
        }}

        # Strip prefix and proxy to backend
        location /{app_name}/ {{
            rewrite ^/{app_name}/(.*)$ /$1 break;
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Prefix /{app_name};
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
        }}

        location = /healthz_down {{
            internal;
            return 503 "service unavailable\n";
            add_header Content-Type text/plain;
        }}
    }}

    # WebSocket connection upgrade map
    map $http_upgrade $connection_upgrade {{
        default upgrade;
        ''      close;
    }}
}}
'''


def inject_routing_envs(env_vars, app_name, host_mode=False):
    # Strip only the routing keys we own. HOSTNAME/HOST are deliberately left in place:
    # removing them here would make the "has the app already set them?" check below always
    # true, so a user-configured HOST would be silently replaced by 0.0.0.0.
    env_vars = [e for e in env_vars if e['name'] not in ('ROOT_PATH', 'UVICORN_ROOT_PATH', 'FORWARDED_ALLOW_IPS')]
    # Host mode (F1b part 2): the app owns the whole path space at its own hostname, so
    # ROOT_PATH/UVICORN_ROOT_PATH must NOT be set — a framework honoring them would keep
    # emitting /{app_name}-prefixed URLs that no longer exist under the host route. Every
    # request still arrives via the ALB, so FORWARDED_ALLOW_IPS stays set either way.
    if host_mode:
        env_vars += [{'name': 'FORWARDED_ALLOW_IPS', 'value': '*'}]
    else:
        env_vars += [
            {'name': 'ROOT_PATH', 'value': f'/{app_name}'},
            {'name': 'UVICORN_ROOT_PATH', 'value': f'/{app_name}'},
            {'name': 'FORWARDED_ALLOW_IPS', 'value': '*'},
        ]
    # Only inject HOSTNAME/HOST if the app hasn't already set them.
    # This forces binding on all interfaces so nginx can reach the app via 127.0.0.1,
    # but avoids breaking apps that rely on HOSTNAME for service discovery.
    if not any(e['name'] in ('HOSTNAME', 'HOST') for e in env_vars):
        env_vars += [
            {'name': 'HOSTNAME', 'value': '0.0.0.0'},
            {'name': 'HOST', 'value': '0.0.0.0'},
        ]
    return env_vars
