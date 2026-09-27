import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass

from aws.alb import ALBClient
from aws.container_config import (
    HOST_MODE_HEALTH_CHECK_PATH,
    generate_nginx_config,
    inject_routing_envs,
)
from aws.eks import EKSClient, assume_deploy_role, cluster_name_from_arn
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException
from shared.k8s.client import k8s_api_client
from shared.k8s.token import mint_eks_token
from shared.mode import is_dev_mode

from api.common.envs.application import app_config
from api.common.host_url import (
    HostUrlNotAvailable,
    build_app_hostname,
    infra_host_ready,
)
from api.common.naming import require_k8s_safe_slug
from api.mock import mock_k8s

logger = logging.getLogger(__name__)

INGRESS_CLASS_NAME = "launchpad-alb"
# Pinned by digest, not tag: a mutable tag means a later rollout can run changed upstream
# content into every customer's cluster with no change in this repository. This is the
# 1.27-alpine manifest list (linux/amd64 + linux/arm64 — EKS Auto Mode provisions both).
# Bump deliberately: re-resolve the tag's digest and update this line in its own commit.
NGINX_IMAGE = (
    "public.ecr.aws/nginx/nginx@sha256:"
    "8f755514b13901f9dc92627c363552ecfedc2e7e13fa36471e5bf0d4188cf21c"
)
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


def _find_group_alb_arn(elbv2_client, dns_name: str) -> str | None:
    """B1: real elbv2 DescribeLoadBalancers has no filter by DNS name (only by ARN or LB
    name) — the shared EKS Auto Mode group ALB's identity is only known to us via its DNS
    name (Environment.alb_dns, captured once at bootstrap — see eks_bootstrap.py), so this
    pages through every load balancer in the account and matches DNSName client-side.
    Read-only (DescribeLoadBalancers/DescribeListeners only) — see
    EKSDeployer._verify_eks_https_listener, the only caller. Never mutates anything: B1's
    fix is scoping the host Ingress itself to HTTPS:443 via a listen-ports annotation, not
    an out-of-band ALB rule this function used to help create."""
    target = dns_name.rstrip(".").lower()
    marker = None
    while True:
        response = elbv2_client.describe_load_balancers(**({"Marker": marker} if marker else {}))
        for lb in response.get("LoadBalancers", []):
            if lb.get("DNSName", "").rstrip(".").lower() == target:
                return lb.get("LoadBalancerArn")
        marker = response.get("NextMarker")
        if not marker:
            return None


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
        # B1 fix: the host-mode rule lives in its own Ingress object (scoped to HTTPS:443
        # via a listen-ports annotation — see EKSDeployer._host_ingress_manifest), not as
        # a second rule on the path Ingress above. Always present in this dict (even for
        # an app that never reaches host mode) — deleting a name that was never created is
        # a tolerated 404, not an error.
        "host_ingress": f"{slug}-host",
    }


@contextmanager
def k8s_apis(session, infrastructure, cluster_name: str, config=None):
    """config, when given, overrides the Config used for the EKS describe_cluster call and
    the {cluster}-deploy AssumeRole (used by the runtime-logs path to bound both inside its
    own request deadline). Every other caller omits it and keeps today's client defaults."""
    dev_mode = is_dev_mode(app_config.mode)
    is_mock = bool(getattr(infrastructure, "is_mock", False))
    if is_mock and not dev_mode:
        raise ValueError("Refusing real Kubernetes access against a mock infrastructure")
    if dev_mode and not is_mock:
        raise ValueError("Refusing mock Kubernetes access against a real infrastructure")

    cluster = EKSClient(session, config=config).describe_cluster(cluster_name)
    if is_mock:
        yield mock_k8s.get_mock_apis(str(infrastructure.id))
        return

    region = session.region_name
    deploy_session = assume_deploy_role(session, infrastructure.code, cluster_name, region, config=config)

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
    pointing at a Service that has already gone. The host-mode Ingress (a separate object —
    see EKSDeployer._host_ingress_manifest) is deleted alongside the path Ingress, before
    anything it points at; a name that was never actually created 404s and is tolerated by
    delete_object."""
    with k8s_apis(session, infrastructure, cluster_name_from_arn(environment.cluster_arn)) as apis:
        host_ingress_name = refs.get("host_ingress")
        if host_ingress_name:
            delete_object(apis, {"kind": "ingress", "namespace": refs.get("namespace"), "name": host_ingress_name})
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
        self.host_mode, self.app_hostname, self.host_reason = self._resolve_host_routing()

    def _resolve_host_routing(self) -> tuple:
        """F1b part 3a, EKS counterpart to application_deployment_service.py's ECS routing
        resolution. Never raises: an app ineligible for host mode simply deploys with its
        existing path-only Ingress rules, exactly as before this feature.

        DB-only — makes no AWS call. This runs from `__init__`, and `__init__` must never
        have a side effect against the customer's account (LOW fix, security review): the
        live ALB check that used to run here too now runs from `deploy()` instead, right
        before it would actually matter, and even that is read-only — see
        `_verify_eks_https_listener`.

        B1 (security review): host mode on EKS is additionally gated behind
        `settings.EKS_HOST_MODE_ENABLED` (default off). Even with a real :443 listener and
        a correct certificate, adding a host rule to a k8s Ingress whose
        IngressClassParams.listenPorts includes `80` used to risk the AWS Load Balancer
        Controller forwarding that hostname on :80 in plaintext — an out-of-band boto3
        redirect rule tried to intercept that and was removed (a controller-owned ALB
        reconciling around unrecognized manual rules is not something to build safety on).
        The fix instead is `_host_ingress_manifest`'s dedicated Ingress, scoped to
        HTTPS:443 only via a per-Ingress `listen-ports` annotation, so no :80 rule for this
        host is ever created by the controller in the first place — but that depends on
        the controller actually honoring a per-Ingress listen-ports override inside a
        shared IngressGroup, unverified against a real cluster (see
        REAL-AWS-VALIDATION.md). The flag keeps this off until that's confirmed.
        """
        from django.conf import settings

        # getattr with a default, not a bare attribute access: matches api/common/host_url.py's
        # PLATFORM_BASE_DOMAIN convention for an optional feature flag, and keeps a settings
        # module that predates this flag (e.g. test_settings.py) from raising AttributeError.
        if not getattr(settings, "EKS_HOST_MODE_ENABLED", False):
            return False, None, "eks_host_mode_disabled"

        ready, reason = infra_host_ready(self.infrastructure)
        if not ready:
            return False, None, reason
        try:
            hostname = build_app_hostname(self.infrastructure.dns_label, self.slug)
        except HostUrlNotAvailable as exc:
            return False, None, exc.reason

        return True, hostname, None

    def _verify_eks_https_listener(self) -> tuple:
        """Read-only defense-in-depth check, run from `deploy()` (never `__init__` — see
        _resolve_host_routing) right before a host-only Ingress would be created: confirms
        the shared group ALB actually has a working :443 listener, since
        `Infrastructure.https_ready` (mirrored from infrastructure-service's
        `Environment.eks_ingress_tls_ready`) is a fast pre-check only — the
        IngressClassParams `listenPorts`/`certificateARNs` patch it reflects has never
        been confirmed against a real cluster to actually reconcile onto the ALB (see
        REAL-AWS-VALIDATION.md). Makes no mutating AWS call — B1's fix is the host
        Ingress's own listen-ports annotation, not anything this function creates.
        `self.session` is already the correctly mock/real-gated session the caller built
        (application_deployment_service.py's _create_aws_session)."""
        if not self.environment.alb_dns:
            return False, "eks_alb_not_discovered"
        try:
            elbv2 = self.session.client("elbv2")
            lb_arn = _find_group_alb_arn(elbv2, self.environment.alb_dns)
            if lb_arn is None:
                return False, "eks_alb_live_check_failed"

            alb = ALBClient(self.session)
            https_listener_arn = alb.get_listener_arn(lb_arn, port=443)
            if not https_listener_arn:
                return False, "eks_https_listener_not_applied"
        except Exception:
            logger.warning(
                "EKS host-mode live check failed for %s (deploying in path mode)",
                self.slug, exc_info=True,
            )
            return False, "eks_alb_live_check_failed"
        return True, None

    def _alb_subnet_cidrs(self) -> list:
        """CIDRs of the subnets the ALB has ENIs in, read from EC2 rather than derived from
        vpc_cidr — the public/private split is a terraform detail and recomputing it here
        would be a second copy free to drift.

        Fails closed: if the subnets cannot be resolved, the ALB peer is simply omitted.
        That breaks external ingress for this app loudly instead of quietly widening the
        policy to every pod in the cluster.
        """
        try:
            ec2 = self.session.client("ec2")
            response = ec2.describe_subnets(Filters=[
                {"Name": "vpc-id", "Values": [self.environment.vpc_id]},
                {"Name": "tag:Type", "Values": ["public"]},
            ])
        except Exception:
            logger.exception(
                "Could not resolve public subnets for vpc %s; the ingress policy for %s will "
                "not admit the load balancer", self.environment.vpc_id, self.slug,
            )
            return []
        return [s["CidrBlock"] for s in response.get("Subnets", []) if s.get("CidrBlock")]

    def deploy(self, image_uri: str, created_resources: list) -> dict:
        # LOW fix (security review): the live ALB check — the one AWS side effect host
        # mode needs before it can be granted — runs here, in deploy(), never in
        # __init__. A downgrade here overrides the __init__-time DB-only candidate state
        # before any manifest below reads self.host_mode/self.app_hostname.
        if self.host_mode:
            live_ready, live_reason = self._verify_eks_https_listener()
            if not live_ready:
                logger.warning(
                    "EKS host-mode live check failed for %s: %s (deploying in path mode)",
                    self.slug, live_reason,
                )
                self.host_mode, self.app_hostname, self.host_reason = False, None, live_reason

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
            if self.host_mode:
                self._record(created_resources, "ingress", self._apply_host_ingress(apis))
            elif self.application.host_route_applied:
                # host_route_applied (persisted from the previous deploy) is the only
                # signal that a host-only Ingress might actually exist — an app that has
                # never been in host mode has nothing to clean up, and skipping the call
                # entirely avoids a k8s API round trip on every ordinary path-mode deploy.
                # TLS/DNS regressed, or the live check above just failed this time — either
                # way, never leave a host-only Ingress routing to a backend no longer
                # expected to serve host-mode traffic.
                delete_object(apis, {"kind": "ingress", "namespace": self.namespace, "name": f"{self.slug}-host"})
            self._wait_for_rollout(apis)
        # host_route_applied reflects what this deploy actually applied, not a pre-flight
        # intent — read back by api/common/host_url.py:app_host_url for the host_url API
        # gate, the EKS counterpart to Application.host_forward_rule_arn on ECS.
        if self.application.host_route_applied != self.host_mode:
            self.application.host_route_applied = self.host_mode
            self.application.save(update_fields=["host_route_applied"])
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
        # Run unconditionally: if any of these failed after the namespace was created, the
        # 409 path would otherwise leave it running forever with no quota and no policies.
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
                metadata=k8s.V1ObjectMeta(name="allow-serving-port"),
                spec=k8s.V1NetworkPolicySpec(
                    pod_selector=k8s.V1LabelSelector(match_labels={"app": self.slug}),
                    policy_types=["Ingress"],
                    ingress=[
                        # The ALB targets pod IPs from outside the cluster, so it cannot be
                        # expressed as a namespace/pod peer. It has to come in as an ipBlock,
                        # but that block must be the ALB's own public subnets and NOT the whole
                        # VPC CIDR: the VPC CNI hands pods addresses out of the private subnets
                        # of that same VPC, so a vpc-wide block silently readmits every other
                        # tenant's pods on this port and undoes the default-deny above.
                        k8s.V1NetworkPolicyIngressRule(
                            _from=[
                                k8s.V1NetworkPolicyPeer(
                                    namespace_selector=k8s.V1LabelSelector(
                                        match_labels={"kubernetes.io/metadata.name": self.namespace}
                                    )
                                ),
                                *(
                                    k8s.V1NetworkPolicyPeer(ip_block=k8s.V1IPBlock(cidr=cidr))
                                    for cidr in self._alb_subnet_cidrs()
                                ),
                            ],
                            ports=[k8s.V1NetworkPolicyPort(port=NGINX_PORT, protocol="TCP")],
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
            data={"nginx.conf": generate_nginx_config(
                self.slug, self.application.port, listen_port=NGINX_PORT,
                host_mode=self.host_mode, app_hostname=self.app_hostname,
            )},
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

    def _apply_host_ingress(self, apis) -> str:
        name = f"{self.slug}-host"
        ingress = self._host_ingress_manifest()
        created = self._create(
            lambda: apis.networking.create_namespaced_ingress(self.namespace, ingress), "Ingress", name
        )
        if not created:
            apis.networking.patch_namespaced_ingress(name, self.namespace, ingress)
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
                # In lockstep with the ALB Ingress's own healthcheck-path annotation (see
                # _ingress_manifest) and the nginx config this deployment just wrote (see
                # _apply_config_map) — host mode serves "/" as the app's own root, not a
                # canned health response, so all three must move together.
                http_get=k8s.V1HTTPGetAction(
                    path=HOST_MODE_HEALTH_CHECK_PATH if self.host_mode else "/", port=NGINX_PORT,
                ),
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
        """Path-mode only — always exactly this one rule, host mode or not. The host-mode
        rule lives in a SEPARATE Ingress (`_host_ingress_manifest`) as of the B1 security
        fix, scoped to `HTTPS: 443` only via its own `listen-ports` annotation. This
        Ingress carries the mirror-image annotation, `HTTP: 80` only: explicit on both
        sides, not a default on one side and an override on the other — see
        `eks_bootstrap.py:apply_eks_tls` for why the shared IngressClassParams itself
        never sets `listenPorts` (class-level would take precedence over both annotations
        at once and reopen exactly what the host Ingress's own annotation exists to
        close). Behavior-identical to every pre-host-mode deploy in every other respect
        (rules, backend, path conditions) when host_mode is False — the object is no
        longer byte-for-byte identical, since it now also carries the explicit
        `listen-ports` annotation above.

        The healthcheck-path annotation still moves with `self.host_mode`: when host mode
        is eligible, this Ingress's backend is the SAME nginx sidecar running host-mode
        config (host URLs are additive, this Ingress keeps serving the path route against
        it), and host-mode nginx answers the canned health response at
        HOST_MODE_HEALTH_CHECK_PATH in every server block, never at "/" — see
        aws/container_config.py.
        """
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
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}]',
                    "alb.ingress.kubernetes.io/healthcheck-path": (
                        HOST_MODE_HEALTH_CHECK_PATH if self.host_mode else "/"
                    ),
                    "alb.ingress.kubernetes.io/success-codes": "200-499",
                },
            ),
            spec=k8s.V1IngressSpec(
                ingress_class_name=INGRESS_CLASS_NAME,
                rules=[k8s.V1IngressRule(http=k8s.V1HTTPIngressRuleValue(paths=paths))],
            ),
        )

    def _host_ingress_manifest(self) -> k8s.V1Ingress:
        """B1 fix: a dedicated Ingress for this app's exact hostname, in the same
        IngressGroup (same `ingress_class_name`, hence the same shared ALB) as the path
        Ingress above, but scoped to `HTTPS: 443` only via its own `listen-ports`
        annotation — the mirror image of the path Ingress's own explicit `HTTP: 80`
        annotation (`_ingress_manifest`). Both are explicit precisely so there is exactly
        one source of truth for listen ports per Ingress: the shared IngressClassParams
        (`eks_bootstrap.py:apply_eks_tls`) deliberately never sets `listenPorts` itself,
        since a class-level value would take precedence over both annotations at once and
        put every Ingress in the group back on both ports. The AWS Load Balancer
        Controller attaches an Ingress's own rules only to the listen ports THAT Ingress
        declares — even though the group's ALB also has a `:80` listener (declared by the
        path Ingress), this Ingress's host rule is never attached to it. No out-of-band
        boto3 ALB rule is created or needed for this — see REAL-AWS-VALIDATION.md for the
        one thing this still depends on unverified: that the controller actually honors a
        per-Ingress listen-ports override inside a shared group the way its docs describe,
        rather than reconciling every group member onto the union of every declared port.

        `alb.ingress.kubernetes.io/ssl-redirect` (a controller-managed `:80` -> `:443`
        redirect for this exact host) was considered and deliberately left out: unclear
        whether it can be scoped to only this Ingress inside a shared group without
        affecting the path Ingress's own `:80` traffic, and unverified is exactly the kind
        of assumption B1 was raised over. Without it, `:80` for this hostname simply hits
        the group ALB's fixed default action (404) — a safe, if less friendly, outcome;
        never a plaintext forward.
        """
        backend = k8s.V1IngressBackend(
            service=k8s.V1IngressServiceBackend(
                name=self.slug, port=k8s.V1ServiceBackendPort(number=80)
            )
        )
        return k8s.V1Ingress(
            metadata=k8s.V1ObjectMeta(
                name=f"{self.slug}-host",
                annotations={
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTPS": 443}]',
                    "alb.ingress.kubernetes.io/healthcheck-path": (
                        HOST_MODE_HEALTH_CHECK_PATH if self.host_mode else "/"
                    ),
                    "alb.ingress.kubernetes.io/success-codes": "200-499",
                },
            ),
            spec=k8s.V1IngressSpec(
                ingress_class_name=INGRESS_CLASS_NAME,
                rules=[k8s.V1IngressRule(
                    # Exact host match — each app owns exactly one hostname, and the ALB
                    # controller resolves which Ingress/rule to route to by matching this
                    # field against the request's Host header.
                    host=self.app_hostname,
                    http=k8s.V1HTTPIngressRuleValue(paths=[
                        k8s.V1HTTPIngressPath(path="/", path_type="Prefix", backend=backend)
                    ]),
                )],
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
