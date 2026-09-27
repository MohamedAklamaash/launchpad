"""Per-application data for infrastructure-service's exit export (F6). Everything here
is built from rows this service already owns — no live AWS or Kubernetes call, no read of
`Application.envs` values. Env *values* never leave this function: only key names (from the
latest successful `Deployment` snapshot, falling back to the live `envs` dict's keys when no
deployment has ever been recorded) cross into the redacted task-definition / manifest text
this module renders.

Task-definition and Kubernetes manifest shapes are re-derived here rather than imported from
`aws/ecs.py` / `api/k8s/deployer.py`: those modules build real boto3/kubernetes-client calls
end to end, and splitting "build the JSON" from "call AWS" out of them is a larger refactor
than this feature needs. Keeping the shapes in sync is a comment-level contract, not a shared
function — see the field-by-field comments below.
"""
import base64
import json
import re
from urllib.parse import urlsplit, urlunsplit

from aws.codebuild import CodeBuildClient
from aws.container_config import generate_nginx_config, inject_routing_envs
from django.conf import settings
from shared.enums.orchestrator import ComputeType

from api.common.naming import app_slug, ecs_log_group, require_k8s_safe_slug
from api.k8s.deployer import NGINX_IMAGE, NGINX_PORT, namespace_for
from api.models.application import Application
from api.models.deployment import Deployment

REDACTED = "<redacted>"


def _scrub_url_userinfo(url: str) -> str:
    """`https://user:ghp_xxx@github.com/org/repo` (embedded userinfo) and
    `https://github.com/org/repo?access_token=ghp_xxx` (a token as a query parameter —
    some hosts accept this as an alternative to userinfo or a header) are both forms
    people paste in for a private repo. `_validate_github_repo` only lowercases/strips
    `.git`, it never strips either. Drop userinfo, the query string, and any fragment
    before the URL crosses into the README, `buildspec/env.example`, or the inventory
    response; the host and path (all a customer needs to find the repo) are untouched."""
    if not url:
        return url
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _env_keys_and_source(application: Application) -> tuple[list[str], dict]:
    """Key names only, plus the shape metadata worth telling the customer about (image
    tag / commit / tag_source), preferring the latest successful `Deployment` snapshot —
    which itself was built without ever storing a value (see `deployment_snapshot.py`) —
    over the live `Application.envs` dict."""
    deployment = (
        Deployment.objects.filter(application=application, status=Deployment.STATUS_SUCCEEDED)
        .order_by("-created_at")
        .first()
    )
    if deployment is not None:
        return list(deployment.env_keys or []), {
            "image_tag": deployment.image_tag,
            "commit_sha": deployment.commit_sha,
            "tag_source": deployment.tag_source,
            "cpu": deployment.cpu,
            "memory": deployment.memory,
            "port": deployment.port,
        }
    return list((application.envs or {}).keys()), {
        "image_tag": None,
        "commit_sha": application.project_commit_hash,
        "tag_source": None,
        "cpu": application.alloted_cpu,
        "memory": application.alloted_memory,
        "port": application.port,
    }


def _task_definition_json(application: Application, env_keys: list[str], shape: dict) -> str:
    slug = app_slug(application.name)
    family = f"{slug}-task"
    port = shape["port"] or application.port
    env_vars = inject_routing_envs([{"name": k, "value": REDACTED} for k in env_keys], slug)
    nginx_config = generate_nginx_config(slug, port)
    container_definitions = [
        {
            "name": family,
            "image": f"<ecr-repo>:{shape['image_tag'] or '<image-tag>'}",
            "essential": True,
            "environment": env_vars,
            "portMappings": [{"containerPort": port, "protocol": "tcp"}],
            "healthCheck": {
                "command": ["CMD-SHELL", f"nc -z 127.0.0.1 {port} || exit 1"],
                "interval": 10, "timeout": 5, "retries": 3, "startPeriod": 60,
            },
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {"awslogs-group": ecs_log_group(slug), "awslogs-stream-prefix": "app"},
            },
        },
        {
            "name": f"{family}-nginx",
            "image": "public.ecr.aws/nginx/nginx:alpine",
            "essential": True,
            "portMappings": [{"containerPort": 80, "protocol": "tcp"}],
            "environment": [{"name": "NGINX_CONFIG_B64", "value": base64.b64encode(nginx_config.encode()).decode()}],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {"awslogs-group": ecs_log_group(slug), "awslogs-stream-prefix": "nginx"},
            },
        },
    ]
    task_def = {
        "family": family,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": str(int((shape["cpu"] or 0.25) * 1024)),
        "memory": str(int((shape["memory"] or 0.5) * 1024)),
        "containerDefinitions": container_definitions,
    }
    return json.dumps(task_def, indent=2)


def _k8s_manifest_json(application: Application, env_keys: list[str], shape: dict) -> str:
    slug = require_k8s_safe_slug(application.name)
    namespace = namespace_for(slug)
    port = shape["port"] or application.port
    app_env = inject_routing_envs(
        [{"name": k, "value": REDACTED} for k in [*env_keys, "PORT"]], slug
    )
    nginx_config = generate_nginx_config(slug, port)
    documents = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": f"{slug}-nginx", "namespace": namespace},
            "data": {"nginx.conf": nginx_config},
        },
        {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": f"{slug}-env", "namespace": namespace},
            "type": "Opaque",
            "stringData": {k: REDACTED for k in env_keys},
        },
        {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": slug, "namespace": namespace, "labels": {"app": slug}},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": slug}},
                "template": {
                    "metadata": {"labels": {"app": slug}},
                    "spec": {
                        "containers": [
                            {
                                "name": f"{slug}-app",
                                "image": f"<image-repo>:{shape['image_tag'] or '<image-tag>'}",
                                "ports": [{"containerPort": port}],
                                "env": app_env,
                            },
                            {
                                "name": f"{slug}-nginx",
                                "image": NGINX_IMAGE,
                                "ports": [{"containerPort": NGINX_PORT}],
                                "volumeMounts": [
                                    {"name": "nginx-config", "mountPath": "/etc/nginx/nginx.conf", "subPath": "nginx.conf"}
                                ],
                            },
                        ],
                        "volumes": [{"name": "nginx-config", "configMap": {"name": f"{slug}-nginx"}}],
                    },
                },
            },
        },
        {
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": slug, "namespace": namespace},
            "spec": {"selector": {"app": slug}, "ports": [{"port": 80, "targetPort": NGINX_PORT}]},
        },
        {
            "apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
            "metadata": {
                "name": slug, "namespace": namespace,
                "annotations": {
                    "alb.ingress.kubernetes.io/healthcheck-path": "/",
                    "alb.ingress.kubernetes.io/success-codes": "200-499",
                },
            },
            "spec": {
                "ingressClassName": "launchpad-alb",
                "rules": [{
                    "http": {"paths": [
                        {"path": f"/{slug}", "pathType": "ImplementationSpecific",
                         "backend": {"service": {"name": slug, "port": {"number": 80}}}},
                        {"path": f"/{slug}/*", "pathType": "ImplementationSpecific",
                         "backend": {"service": {"name": slug, "port": {"number": 80}}}},
                    ]}
                }],
            },
        },
    ]
    return json.dumps(documents, indent=2)


def buildspec_text() -> str:
    """The exact static template `CodeBuildClient` uses to build every app's image.
    `_get_buildspec` never touches `self`, so this reuses it without a boto3 session — the
    single source of truth for what actually runs stays `aws/codebuild.py`."""
    return CodeBuildClient._get_buildspec(None)


def codebuild_project_name(infra_id) -> str:
    return re.sub(r"[^a-zA-Z0-9\-_]", "", f"launchpad-build-{infra_id}")


def codebuild_role_name(infra_id) -> str:
    return f"launchpad-codebuild-role-{infra_id}"


def app_export_data(application: Application) -> dict:
    """One app's exit-export inventory entry. Contains no env value, no webhook secret,
    no GitHub token — only key names, ARNs already on the row, and rendered (redacted)
    manifest/task-def text."""
    env_keys, shape = _env_keys_and_source(application)
    compute_type = application.infrastructure.compute_type
    has_webhook_secret = bool(application.github_webhook_secret)

    entry = {
        "id": str(application.id),
        "name": application.name,
        "slug": app_slug(application.name),
        "repo_url": _scrub_url_userinfo(application.project_remote_url),
        "branch": application.project_branch,
        "port": shape["port"] or application.port,
        "dockerfile_path": application.dockerfile_path,
        "build_context": application.build_context,
        "cpu": shape["cpu"],
        "memory": shape["memory"],
        "env_keys": env_keys,
        "image_tag": shape["image_tag"],
        "commit_sha": shape["commit_sha"],
        "tag_source": shape["tag_source"],
        "status": application.status,
        "auto_deploy_paused": application.auto_deploy_paused,
        "task_definition_arn": application.task_definition_arn,
        "service_arn": application.service_arn,
        "target_group_arn": application.target_group_arn,
        "listener_rule_arn": application.listener_rule_arn,
        "runtime_refs": application.runtime_refs,
        "has_webhook_secret": has_webhook_secret,
        "webhook_url": (
            f"{settings.PUBLIC_GATEWAY_URL}/api/webhooks/github/{application.id}"
            if has_webhook_secret else None
        ),
        "task_definition_json": None,
        "k8s_manifest_json": None,
    }
    if compute_type == ComputeType.EKS:
        entry["k8s_manifest_json"] = _k8s_manifest_json(application, env_keys, shape)
    else:
        entry["task_definition_json"] = _task_definition_json(application, env_keys, shape)
    return entry


def export_data_for_infrastructure(infra_id) -> dict:
    apps = Application.objects.filter(infrastructure_id=infra_id).order_by("name")
    return {
        "infrastructure_id": str(infra_id),
        "apps": [app_export_data(app) for app in apps],
        "codebuild": {
            "project_name": codebuild_project_name(infra_id),
            "role_name": codebuild_role_name(infra_id),
        },
        "buildspec": buildspec_text(),
    }
