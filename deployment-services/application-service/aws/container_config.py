def generate_nginx_config(app_name, backend_port, listen_port=80):
    """NGINX config that strips the /{app_name} prefix and injects X-Forwarded-Prefix.
    The ALB Ingress controller cannot rewrite paths (rewrite-target is nginx-ingress-only),
    so this sidecar is load-bearing on EKS as well as ECS."""
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


def inject_routing_envs(env_vars, app_name):
    # Strip only the routing keys we own. HOSTNAME/HOST are deliberately left in place:
    # removing them here would make the "has the app already set them?" check below always
    # true, so a user-configured HOST would be silently replaced by 0.0.0.0.
    env_vars = [e for e in env_vars if e['name'] not in ('ROOT_PATH', 'UVICORN_ROOT_PATH', 'FORWARDED_ALLOW_IPS')]
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
