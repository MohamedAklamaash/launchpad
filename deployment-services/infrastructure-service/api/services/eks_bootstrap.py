import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import boto3
from api.common.envs.application import app_config
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException
from shared.k8s.client import k8s_api_client
from shared.k8s.token import mint_eks_token
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

BOOTSTRAP_NAMESPACE = "launchpad-bootstrap"
INGRESS_CLASS_NAME = "launchpad-alb"
DEFAULT_BACKEND_SERVICE = "default-backend"
ALB_POLL_TIMEOUT_SECONDS = 600
ALB_POLL_INTERVAL_SECONDS = 15
ALB_TIMEOUT_MARKER = "EKS_BOOTSTRAP_ALB_TIMED_OUT"


class EksBootstrapError(Exception):
    def __init__(self, message: str, logs: str = ""):
        super().__init__(message)
        self.logs = logs


class EksBootstrapTimeout(EksBootstrapError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    alb_dns: str
    logs: str


def phase_marker(phase: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"[{timestamp}] [phase:{phase}]"


def bootstrap_eks_environment(infra, *, credentials: dict, region: str, cluster_name: str) -> BootstrapResult:
    if is_dev_mode(app_config.mode) or getattr(infra, "is_mock", False):
        raise EksBootstrapError("EKS bootstrap must never run against a dev/mock infrastructure")

    lines = [phase_marker("ingress-bootstrap")]
    try:
        session = _boto_session(credentials, region)
        cluster = session.client("eks").describe_cluster(name=cluster_name)["cluster"]
        with k8s_api_client(
            infra,
            app_config.mode,
            endpoint=cluster["endpoint"],
            ca_data=cluster["certificateAuthority"]["data"],
            token=mint_eks_token(session, cluster_name, region),
            token_provider=lambda: mint_eks_token(session, cluster_name, region),
        ) as api:
            _enable_network_policy_enforcement(api, lines)
            _ensure_ingress_class(api, f"launchpad-{str(infra.id)[:8]}", lines)
            _ensure_bootstrap_ingress(api, lines)
            lines.append(phase_marker("alb-wait"))
            alb_dns = _wait_for_alb_hostname(api, lines)
        lines.append(f"[alb-wait] alb_dns={alb_dns}")
        return BootstrapResult(alb_dns=alb_dns, logs="\n".join(lines))
    except EksBootstrapError:
        raise
    except Exception as e:
        lines.append(f"[bootstrap-error] {e}")
        raise EksBootstrapError(str(e), logs="\n".join(lines)) from e


def _boto_session(credentials: dict, region: str) -> boto3.Session:
    return boto3.Session(
        aws_access_key_id=credentials.get("aws_access_key_id"),
        aws_secret_access_key=credentials.get("aws_secret_access_key"),
        aws_session_token=credentials.get("aws_session_token"),
        region_name=region,
    )


def _get_or_create(create, kind: str, lines: list):
    try:
        create()
        lines.append(f"[k8s] created {kind}")
    except ApiException as e:
        if e.status != 409:
            raise
        lines.append(f"[k8s] {kind} already exists")


def _enable_network_policy_enforcement(api, lines: list):
    core = k8s.CoreV1Api(api)
    enabled = {"enable-network-policy-controller": "true"}
    try:
        config_map = core.read_namespaced_config_map("amazon-vpc-cni", "kube-system")
        if (config_map.data or {}).get("enable-network-policy-controller") != "true":
            core.patch_namespaced_config_map("amazon-vpc-cni", "kube-system", {"data": enabled})
            lines.append("[k8s] enabled network-policy controller in amazon-vpc-cni ConfigMap")
        else:
            lines.append("[k8s] network-policy controller already enabled")
    except ApiException as e:
        if e.status != 404:
            raise
        _get_or_create(
            lambda: core.create_namespaced_config_map(
                "kube-system",
                k8s.V1ConfigMap(metadata=k8s.V1ObjectMeta(name="amazon-vpc-cni"), data=enabled),
            ),
            "amazon-vpc-cni ConfigMap",
            lines,
        )

    # The ConfigMap above is the documented enable step for Auto Mode. NodeClass
    # spec.networkPolicy is an optional knob whose default is already DefaultAllow, so
    # writing that value would change nothing while reading like enforcement was turned
    # on. Enforcement is proven by the sandbox connectivity check, not by this call.


def _ensure_ingress_class(api, group_name: str, lines: list):
    custom = k8s.CustomObjectsApi(api)
    ingress_class_params = {
        "apiVersion": "eks.amazonaws.com/v1",
        "kind": "IngressClassParams",
        "metadata": {"name": INGRESS_CLASS_NAME},
        "spec": {"scheme": "internet-facing", "group": {"name": group_name}},
    }
    _get_or_create(
        lambda: custom.create_cluster_custom_object("eks.amazonaws.com", "v1", "ingressclassparams", ingress_class_params),
        "IngressClassParams",
        lines,
    )

    networking = k8s.NetworkingV1Api(api)
    ingress_class = k8s.V1IngressClass(
        metadata=k8s.V1ObjectMeta(name=INGRESS_CLASS_NAME),
        spec=k8s.V1IngressClassSpec(
            controller="eks.amazonaws.com/alb",
            parameters=k8s.V1IngressClassParametersReference(
                api_group="eks.amazonaws.com", kind="IngressClassParams", name=INGRESS_CLASS_NAME
            ),
        ),
    )
    _get_or_create(lambda: networking.create_ingress_class(ingress_class), "IngressClass", lines)


def _ensure_bootstrap_ingress(api, lines: list):
    core = k8s.CoreV1Api(api)
    _get_or_create(
        lambda: core.create_namespace(k8s.V1Namespace(metadata=k8s.V1ObjectMeta(name=BOOTSTRAP_NAMESPACE))),
        f"Namespace/{BOOTSTRAP_NAMESPACE}",
        lines,
    )
    service = k8s.V1Service(
        metadata=k8s.V1ObjectMeta(name=DEFAULT_BACKEND_SERVICE),
        spec=k8s.V1ServiceSpec(
            type="ClusterIP",
            selector={"app": DEFAULT_BACKEND_SERVICE},
            ports=[k8s.V1ServicePort(port=80, target_port=80)],
        ),
    )
    _get_or_create(
        lambda: core.create_namespaced_service(BOOTSTRAP_NAMESPACE, service),
        f"Service/{DEFAULT_BACKEND_SERVICE}",
        lines,
    )
    networking = k8s.NetworkingV1Api(api)
    ingress = k8s.V1Ingress(
        metadata=k8s.V1ObjectMeta(name="bootstrap"),
        spec=k8s.V1IngressSpec(
            ingress_class_name=INGRESS_CLASS_NAME,
            default_backend=k8s.V1IngressBackend(
                service=k8s.V1IngressServiceBackend(
                    name=DEFAULT_BACKEND_SERVICE,
                    port=k8s.V1ServiceBackendPort(number=80),
                )
            ),
        ),
    )
    _get_or_create(
        lambda: networking.create_namespaced_ingress(BOOTSTRAP_NAMESPACE, ingress),
        "Ingress/bootstrap",
        lines,
    )


def _wait_for_alb_hostname(api, lines: list) -> str:
    networking = k8s.NetworkingV1Api(api)
    deadline = time.monotonic() + ALB_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        ingress = networking.read_namespaced_ingress("bootstrap", BOOTSTRAP_NAMESPACE)
        entries = (ingress.status.load_balancer.ingress if ingress.status and ingress.status.load_balancer else None) or []
        if entries and entries[0].hostname:
            return entries[0].hostname
        time.sleep(ALB_POLL_INTERVAL_SECONDS)
    lines.append(f"[alb-wait] {ALB_TIMEOUT_MARKER} after {ALB_POLL_TIMEOUT_SECONDS}s")
    raise EksBootstrapTimeout(
        f"{ALB_TIMEOUT_MARKER}: no ALB hostname on Ingress/bootstrap after {ALB_POLL_TIMEOUT_SECONDS}s",
        logs="\n".join(lines),
    )
