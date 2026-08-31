import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass

from kubernetes import client as k8s
from kubernetes.client.rest import ApiException

from api.common.envs.application import app_config
from api.common.naming import require_k8s_safe_slug
from api.mock import mock_k8s
from aws.container_config import generate_nginx_config, inject_routing_envs
from aws.eks import EKSClient, assume_deploy_role, cluster_name_from_arn
from shared.k8s.client import k8s_api_client
from shared.k8s.token import mint_eks_token
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

INGRESS_CLASS_NAME = "launchpad-alb"
NGINX_IMAGE = "public.ecr.aws/nginx/nginx:alpine"
# NET_BIND_SERVICE is dropped along with every other capability, so the sidecar
# cannot bind port 80.
NGINX_PORT = 18080
SIDECAR_CPU_MILLI = 100
SIDECAR_MEMORY_MI = 128
SIDECAR_RESOURCES = {"cpu": f"{SIDECAR_CPU_MILLI}m", "memory": f"{SIDECAR_MEMORY_MI}Mi"}
ROLLOUT_TIMEOUT_SECONDS = 600
ROLLOUT_POLL_INTERVAL_SECONDS = 10
MAX_FAILURE_MESSAGE_CHARS = 4000
ORDERED_DELETE_KINDS = ("ingress", "service", "deployment", "configmap", "namespace")


@dataclass(frozen=True, slots=True)
class K8sApis:
    core: object
    apps: object
    networking: object


def namespace_for(slug: str) -> str:
    # Namespace per application, not per infrastructure: app names are only unique per
    # infrastructure, so two owners on a shared infra would otherwise share object names.
    return f"app-{slug}"


def runtime_refs_for(slug: str) -> dict:
    return {
        "runtime": "eks",
        "namespace": namespace_for(slug),
        "configmap": f"{slug}-nginx",
        "deployment": slug,
        "service": slug,
        "ingress": slug,
    }


@contextmanager
def k8s_apis(session, infrastructure, cluster_name: str):
    dev_mode = is_dev_mode(app_config.mode)
    is_mock = bool(getattr(infrastructure, "is_mock", False))
    if is_mock and not dev_mode:
        raise ValueError("Refusing real Kubernetes access against a mock infrastructure")
    if dev_mode and not is_mock:
        raise ValueError("Refusing mock Kubernetes access against a real infrastructure")

    cluster = EKSClient(session).describe_cluster(cluster_name)
    if is_mock:
        yield mock_k8s.get_mock_apis(str(infrastructure.id))
        return

    region = session.region_name
    deploy_session = assume_deploy_role(session, infrastructure.code, cluster_name, region)

    def mint():
        return mint_eks_token(deploy_session, cluster_name, region)

    with k8s_api_client(
        infrastructure,
        app_config.mode,
        endpoint=cluster["endpoint"],
        ca_data=cluster["ca_data"],
        token=mint(),
        token_provider=mint,
    ) as api:
        yield K8sApis(
            core=k8s.CoreV1Api(api),
            apps=k8s.AppsV1Api(api),
            networking=k8s.NetworkingV1Api(api),
        )


def delete_runtime_resources(session, infrastructure, environment, refs: dict):
    """Ingress → Service → Deployment → ConfigMap → Namespace: never strand a live Ingress
    pointing at a Service that has already gone."""
    with k8s_apis(session, infrastructure, cluster_name_from_arn(environment.cluster_arn)) as apis:
        for kind in ORDERED_DELETE_KINDS:
            name = refs.get(kind)
            if name:
                delete_object(apis, {"kind": kind, "namespace": refs.get("namespace"), "name": name})


def delete_object(apis, ref: dict):
    kind, name = ref["kind"], ref["name"]
    namespace = ref.get("namespace")
    deleters = {
        "ingress": lambda: apis.networking.delete_namespaced_ingress(name, namespace),
        "service": lambda: apis.core.delete_namespaced_service(name, namespace),
        "deployment": lambda: apis.apps.delete_namespaced_deployment(name, namespace),
        "configmap": lambda: apis.core.delete_namespaced_config_map(name, namespace),
        "namespace": lambda: apis.core.delete_namespace(name),
    }
    if kind not in deleters:
        raise ValueError(f"Unknown Kubernetes resource kind: {kind}")
    try:
        deleters[kind]()
        logger.info(f"Deleted {kind} {name} in {namespace}")
    except ApiException as e:
        if e.status != 404:
            raise
        logger.info(f"{kind} {name} already absent in {namespace}")


class RolloutFailed(Exception):
    pass


class EKSDeployer:
    def __init__(self, session, application, environment):
        self.session = session
        self.application = application
        self.infrastructure = application.infrastructure
        self.environment = environment
        self.slug = require_k8s_safe_slug(application.name)
        self.namespace = namespace_for(self.slug)
        self.cluster_name = cluster_name_from_arn(environment.cluster_arn)

    def deploy(self, image_uri: str, created_resources: list) -> dict:
        # Persist the handles before creating anything: a worker that dies mid-deploy must
        # still leave the cleanup path something to find.
        refs = runtime_refs_for(self.slug)
        self.application.runtime_refs = refs
        self.application.save(update_fields=["runtime_refs"])
        with self._apis() as apis:
            self._record(created_resources, "namespace", self._ensure_namespace(apis))
            self._record(created_resources, "configmap", self._apply_config_map(apis))
            self._record(created_resources, "deployment", self._apply_deployment(apis, image_uri))
            self._record(created_resources, "service", self._apply_service(apis))
            self._record(created_resources, "ingress", self._apply_ingress(apis))
            self._wait_for_rollout(apis)
        return refs

    def delete_object(self, ref: dict):
        with self._apis() as apis:
            delete_object(apis, ref)

    def _record(self, created_resources: list, kind: str, name: str):
        if name:
            created_resources.append(("k8s_object", {"kind": kind, "namespace": self.namespace, "name": name}))

    # --- object creation ---------------------------------------------------

    def _create(self, create, kind: str, name: str) -> str:
        """Returns the name only when this call created it, so the failure unwind never
        deletes an object a previous successful deploy owns."""
        try:
            create()
            logger.info(f"Created {kind} {name} in {self.namespace}")
            return name
        except ApiException as e:
            if e.status != 409:
                raise
            logger.info(f"{kind} {name} already exists in {self.namespace}")
            return None

    def _ensure_namespace(self, apis) -> str:
        namespace = k8s.V1Namespace(
            metadata=k8s.V1ObjectMeta(
                name=self.namespace,
                labels={
                    "pod-security.kubernetes.io/enforce": "baseline",
                    "app.kubernetes.io/managed-by": "launchpad",
                },
            )
        )
        created = self._create(lambda: apis.core.create_namespace(namespace), "Namespace", self.namespace)
        if created:
            self._create_quota(apis)
            self._create_limit_range(apis)
            self._create_network_policies(apis)
        return created

    def _create_quota(self, apis):
        cpu, memory = self._pod_totals()
        quota = k8s.V1ResourceQuota(
            metadata=k8s.V1ObjectMeta(name="launchpad-quota"),
            spec=k8s.V1ResourceQuotaSpec(
                hard={
                    "requests.cpu": f"{2 * cpu}m",
                    "requests.memory": f"{2 * memory}Mi",
                    "limits.cpu": f"{2 * cpu}m",
                    "limits.memory": f"{2 * memory}Mi",
                    "pods": "4",
                }
            ),
        )
        self._create(
            lambda: apis.core.create_namespaced_resource_quota(self.namespace, quota),
            "ResourceQuota", "launchpad-quota",
        )

    def _create_limit_range(self, apis):
        app_resources = self._app_resources()
        limit_range = k8s.V1LimitRange(
            metadata=k8s.V1ObjectMeta(name="launchpad-limits"),
            spec=k8s.V1LimitRangeSpec(
                limits=[
                    k8s.V1LimitRangeItem(
                        type="Container", default=app_resources, default_request=app_resources
                    )
                ]
            ),
        )
        self._create(
            lambda: apis.core.create_namespaced_limit_range(self.namespace, limit_range),
            "LimitRange", "launchpad-limits",
        )

    def _create_network_policies(self, apis):
        policies = [
            k8s.V1NetworkPolicy(
                metadata=k8s.V1ObjectMeta(name="default-deny-ingress"),
                spec=k8s.V1NetworkPolicySpec(pod_selector=k8s.V1LabelSelector(), policy_types=["Ingress"]),
            ),
            k8s.V1NetworkPolicy(
                metadata=k8s.V1ObjectMeta(name="allow-alb-to-nginx"),
                spec=k8s.V1NetworkPolicySpec(
                    pod_selector=k8s.V1LabelSelector(match_labels={"app": self.slug}),
                    policy_types=["Ingress"],
                    ingress=[
                        k8s.V1NetworkPolicyIngressRule(
                            ports=[k8s.V1NetworkPolicyPort(port=NGINX_PORT, protocol="TCP")]
                        )
                    ],
                ),
            ),
            k8s.V1NetworkPolicy(
                metadata=k8s.V1ObjectMeta(name="allow-egress"),
                spec=k8s.V1NetworkPolicySpec(
                    pod_selector=k8s.V1LabelSelector(),
                    policy_types=["Egress"],
                    egress=[k8s.V1NetworkPolicyEgressRule()],
                ),
            ),
        ]
        for policy in policies:
            self._create(
                lambda p=policy: apis.networking.create_namespaced_network_policy(self.namespace, p),
                "NetworkPolicy", policy.metadata.name,
            )

    def _apply_config_map(self, apis) -> str:
        name = f"{self.slug}-nginx"
        config_map = k8s.V1ConfigMap(
            metadata=k8s.V1ObjectMeta(name=name),
            data={"nginx.conf": generate_nginx_config(self.slug, self.application.port, listen_port=NGINX_PORT)},
        )
        created = self._create(
            lambda: apis.core.create_namespaced_config_map(self.namespace, config_map), "ConfigMap", name
        )
        if not created:
            apis.core.patch_namespaced_config_map(name, self.namespace, config_map)
        return created

    def _apply_deployment(self, apis, image_uri: str) -> str:
        deployment = self._deployment_manifest(image_uri)
        created = self._create(
            lambda: apis.apps.create_namespaced_deployment(self.namespace, deployment), "Deployment", self.slug
        )
        if not created:
            apis.apps.patch_namespaced_deployment(self.slug, self.namespace, deployment)
        return created

    def _apply_service(self, apis) -> str:
        service = k8s.V1Service(
            metadata=k8s.V1ObjectMeta(name=self.slug),
            spec=k8s.V1ServiceSpec(
                type="ClusterIP",
                selector={"app": self.slug},
                ports=[k8s.V1ServicePort(port=80, target_port=NGINX_PORT, protocol="TCP")],
            ),
        )
        created = self._create(
            lambda: apis.core.create_namespaced_service(self.namespace, service), "Service", self.slug
        )
        if not created:
            apis.core.patch_namespaced_service(self.slug, self.namespace, service)
        return created

    def _apply_ingress(self, apis) -> str:
        ingress = self._ingress_manifest()
        created = self._create(
            lambda: apis.networking.create_namespaced_ingress(self.namespace, ingress), "Ingress", self.slug
        )
        if not created:
            apis.networking.patch_namespaced_ingress(self.slug, self.namespace, ingress)
        return created

    # --- manifests ---------------------------------------------------------

    def _app_resources(self) -> dict:
        cpu = self.application.alloted_cpu or 0.25
        memory = self.application.alloted_memory or 0.5
        return {"cpu": f"{int(cpu * 1000)}m", "memory": f"{int(memory * 1024)}Mi"}

    def _pod_totals(self) -> tuple:
        cpu = int((self.application.alloted_cpu or 0.25) * 1000) + SIDECAR_CPU_MILLI
        memory = int((self.application.alloted_memory or 0.5) * 1024) + SIDECAR_MEMORY_MI
        return cpu, memory

    def _app_env(self) -> list:
        envs = {**(self.application.envs or {}), "PORT": str(self.application.port)}
        env_vars = inject_routing_envs([{"name": k, "value": str(v)} for k, v in envs.items()], self.slug)
        return [k8s.V1EnvVar(name=e["name"], value=e["value"]) for e in env_vars]

    def _security_context(self) -> k8s.V1SecurityContext:
        # No runAsNonRoot: customer Dockerfiles routinely run as root and would CrashLoop.
        return k8s.V1SecurityContext(
            allow_privilege_escalation=False,
            capabilities=k8s.V1Capabilities(drop=["ALL"]),
        )

    def _deployment_manifest(self, image_uri: str) -> k8s.V1Deployment:
        app_container = k8s.V1Container(
            name=f"{self.slug}-app",
            image=image_uri,
            ports=[k8s.V1ContainerPort(container_port=self.application.port)],
            env=self._app_env(),
            resources=k8s.V1ResourceRequirements(
                requests=self._app_resources(), limits=self._app_resources()
            ),
            security_context=self._security_context(),
        )
        nginx_container = k8s.V1Container(
            name=f"{self.slug}-nginx",
            image=NGINX_IMAGE,
            ports=[k8s.V1ContainerPort(container_port=NGINX_PORT)],
            volume_mounts=[
                k8s.V1VolumeMount(
                    name="nginx-config", mount_path="/etc/nginx/nginx.conf", sub_path="nginx.conf"
                )
            ],
            resources=k8s.V1ResourceRequirements(
                requests=SIDECAR_RESOURCES,
                limits=SIDECAR_RESOURCES,
            ),
            readiness_probe=k8s.V1Probe(
                http_get=k8s.V1HTTPGetAction(path="/", port=NGINX_PORT),
                initial_delay_seconds=5,
                period_seconds=10,
            ),
            security_context=self._security_context(),
        )
        return k8s.V1Deployment(
            metadata=k8s.V1ObjectMeta(name=self.slug, labels={"app": self.slug}),
            spec=k8s.V1DeploymentSpec(
                replicas=1,
                selector=k8s.V1LabelSelector(match_labels={"app": self.slug}),
                template=k8s.V1PodTemplateSpec(
                    metadata=k8s.V1ObjectMeta(labels={"app": self.slug}),
                    spec=k8s.V1PodSpec(
                        automount_service_account_token=False,
                        containers=[app_container, nginx_container],
                        volumes=[
                            k8s.V1Volume(
                                name="nginx-config",
                                config_map=k8s.V1ConfigMapVolumeSource(name=f"{self.slug}-nginx"),
                            )
                        ],
                    ),
                ),
            ),
        )

    def _ingress_manifest(self) -> k8s.V1Ingress:
        backend = k8s.V1IngressBackend(
            service=k8s.V1IngressServiceBackend(
                name=self.slug, port=k8s.V1ServiceBackendPort(number=80)
            )
        )
        paths = [
            k8s.V1HTTPIngressPath(path=path, path_type="ImplementationSpecific", backend=backend)
            for path in (f"/{self.slug}", f"/{self.slug}/*")
        ]
        return k8s.V1Ingress(
            metadata=k8s.V1ObjectMeta(
                name=self.slug,
                annotations={
                    "alb.ingress.kubernetes.io/healthcheck-path": "/",
                    "alb.ingress.kubernetes.io/success-codes": "200-499",
                },
            ),
            spec=k8s.V1IngressSpec(
                ingress_class_name=INGRESS_CLASS_NAME,
                rules=[k8s.V1IngressRule(http=k8s.V1HTTPIngressRuleValue(paths=paths))],
            ),
        )

    def _apis(self):
        return k8s_apis(self.session, self.infrastructure, self.cluster_name)

    # --- rollout -----------------------------------------------------------

    def _wait_for_rollout(self, apis):
        deadline = time.monotonic() + ROLLOUT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            deployment = apis.apps.read_namespaced_deployment(self.slug, self.namespace)
            available = (deployment.status.available_replicas if deployment.status else 0) or 0
            logger.info(f"Deployment {self.slug}: {available}/1 available replicas")
            if available >= 1:
                return
            time.sleep(ROLLOUT_POLL_INTERVAL_SECONDS)
        raise RolloutFailed(
            f"Deployment {self.slug} had no available replicas after {ROLLOUT_TIMEOUT_SECONDS}s.\n"
            + self._rollout_diagnostics(apis)
        )

    def _rollout_diagnostics(self, apis) -> str:
        lines = []
        try:
            for pod in apis.core.list_namespaced_pod(self.namespace, label_selector=f"app={self.slug}").items:
                for container_status in pod.status.container_statuses or []:
                    described = _describe_container_state(container_status.state)
                    if described:
                        lines.append(f"{pod.metadata.name}/{container_status.name}: {described}")
            for event in apis.core.list_namespaced_event(self.namespace).items:
                if event.type == "Warning":
                    lines.append(f"event {event.reason}: {event.message}")
        except Exception as e:
            lines.append(f"could not harvest pod diagnostics: {e}")
        return "\n".join(lines)[:MAX_FAILURE_MESSAGE_CHARS]

def _describe_container_state(state) -> str:
    if state is None:
        return ""
    if state.waiting:
        return f"{state.waiting.reason}: {state.waiting.message}"
    if state.terminated:
        return f"terminated {state.terminated.reason} (exit {state.terminated.exit_code})"
    return ""
