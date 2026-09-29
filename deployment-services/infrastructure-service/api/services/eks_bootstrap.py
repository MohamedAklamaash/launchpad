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

# The deploy role's access entry (infra/aws/modules/eks/main.tf) grants AmazonEKSEditPolicy
# scoped to namespaces app-* and launchpad-bootstrap. Two things the deployer
# (application-service api/k8s/deployer.py) does are out of that policy's reach: creating and
# deleting its app-{slug} namespace (cluster-scoped), and creating the namespace's
# ResourceQuota and LimitRange (Kubernetes `edit`, and even `admin`, only read those, since
# they are meant to be the cluster admin's guardrail on the namespace). Rather than widen the
# access entry, the entry gets a Kubernetes group, and this cluster-admin bootstrap step
# grants that group a ClusterRole for exactly those verbs. RBAC can't limit a cluster-scoped
# grant to a name prefix, so a ValidatingAdmissionPolicy does it: any write by the group to
# namespaces, resourcequotas or limitranges outside app-* is denied at admission, which
# keeps the deploy role away from kube-system and every non-app namespace.
DEPLOYER_GROUP = "launchpad:deployers"
NAMESPACE_CREATOR_CLUSTER_ROLE = "launchpad-namespace-creator"
APP_NAMESPACE_PREFIX = "app-"
DEPLOYER_NAMESPACE_GUARD = "launchpad-deployers-app-namespaces-only"


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
            _ensure_namespace_creator_rbac(api, lines)
            _ensure_ingress_class(api, _ingress_group_name(infra), lines)
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


def apply_eks_tls(infra, *, credentials: dict, region: str, cluster_name: str, cert_arn: str,
                   infra_is_mock: bool, dev_mode: bool) -> bool:
    """F1b part 3a: the EKS counterpart to the ECS 443 listener — patch the cluster's shared
    IngressClassParams (created once by _ensure_ingress_class, which never updates it) with
    this infra's ACM certificate, once TLS is ISSUED. Called from run_worker.py's TLS
    re-check tick, the same trigger that re-enqueues an ECS provision to apply its 443
    listener.

    Deliberately does NOT set `spec.listenPorts` here (B1 security review, second round):
    the AWS Load Balancer Controller documents IngressClassParams fields as overriding the
    equivalent annotation on every Ingress that uses the class, so a class-level
    `listenPorts` covering both 80 and 443 would silently widen every Ingress's own
    per-Ingress `alb.ingress.kubernetes.io/listen-ports` annotation back onto both ports —
    including the host-only Ingress `api/k8s/deployer.py:_host_ingress_manifest` scopes to
    HTTPS:443 specifically to keep a host rule off :80. Listen ports are scoped entirely at
    the Ingress level instead: exactly one source of truth per Ingress, never a class-level
    value pulling a different direction.

    Also patches the bootstrap Ingress's own `listen-ports` annotation to add `HTTPS: 443`
    (it is `HTTP: 80` only at `_ensure_bootstrap_ingress` time, since no certificate exists
    yet there and an ALB HTTPS listener cannot be created without one). This call is the
    first point where a certificate is actually known to exist, so it is the right place —
    and the earliest possible one — for something to declare `:443` on the group ahead of
    any app-specific Ingress. Without this, `EKSDeployer._verify_eks_https_listener` would
    permanently refuse host mode for every EKS infra: it requires a `:443` listener to
    already exist before granting host mode, and the only other thing that would declare
    one is the per-app host Ingress that check itself gates — a deadlock. The class patch
    runs first so the certificate is already resolvable by the time the Ingress patch
    below triggers the controller's reconcile of it.

    Idempotent: a merge patch with unchanged values is a no-op against an already-patched
    cluster (both patches). Mirrors cert_bootstrap._acm_client's mock/real gate exactly —
    both mismatches raise. In mock/dev there is no real cluster to patch
    (bootstrap_eks_environment already refuses to run against one), so this returns True
    immediately without any k8s call, letting the mock end-to-end flow set
    env.eks_ingress_tls_ready the same way a real patch would.
    """
    from api.services.platform_dns.route53_client import MockRealMismatch

    if infra_is_mock and not dev_mode:
        raise MockRealMismatch("refusing EKS TLS patch for a mock infrastructure outside dev mode")
    if dev_mode and not infra_is_mock:
        raise MockRealMismatch("refusing real EKS TLS patch against a mock infrastructure check inside dev mode")
    if dev_mode:
        return True

    session = _boto_session(credentials, region)
    cluster = session.client("eks").describe_cluster(name=cluster_name)["cluster"]
    with k8s_api_client(
        infra, app_config.mode,
        endpoint=cluster["endpoint"], ca_data=cluster["certificateAuthority"]["data"],
        token=mint_eks_token(session, cluster_name, region),
        token_provider=lambda: mint_eks_token(session, cluster_name, region),
    ) as api:
        custom = k8s.CustomObjectsApi(api)
        custom.patch_cluster_custom_object(
            "eks.amazonaws.com", "v1", "ingressclassparams", INGRESS_CLASS_NAME,
            {"spec": {"certificateARNs": [cert_arn]}},
        )
        networking = k8s.NetworkingV1Api(api)
        networking.patch_namespaced_ingress(
            "bootstrap", BOOTSTRAP_NAMESPACE,
            {"metadata": {"annotations": {
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}, {"HTTPS": 443}]',
            }}},
        )
    return True


def _ingress_group_name(infra) -> str:
    """The ALB Auto Mode group name shared by every Ingress in this cluster's
    IngressClassParams.

    `dns_label` (an independent random token, CLAUDE.md) replaces the old
    `str(infra.id)[:8]` — a UUIDv7 prefix repeats every ~65s platform-wide and is
    forceable from this row's own `created_at`, so two infrastructures created moments
    apart could be handed the same ALB group. Falls back to the old prefix only for an
    infra that somehow has no dns_label yet (pre-F1b rows, or a race before mint_dns_label
    runs) — never raises, since a missing group name must not block EKS bootstrap.

    Safe for an already-bootstrapped cluster: `_ensure_ingress_class` only *creates* the
    IngressClassParams object (`_get_or_create` swallows 409 and does not update it), so a
    cluster whose object already exists under the old formula keeps that group name
    forever regardless of what this function computes on a later bootstrap call — no
    ALB recreation, no edge cutover, no downtime. Only a cluster bootstrapping for the
    first time after this change gets the new dns_label-based group.
    """
    if infra.dns_label:
        return f"launchpad-{infra.dns_label}"
    return f"launchpad-{str(infra.id)[:8]}"


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


def _ensure_namespace_creator_rbac(api, lines: list):
    """Grants the deploy role's Kubernetes group (bound via kubernetes_groups on its EKS
    access entry, infra/aws/modules/eks/main.tf) the cluster-scoped verbs the deployer needs,
    and installs the admission guard confining them to app-*. See DEPLOYER_GROUP's comment.

    Replaces rather than get-or-creates: a cluster bootstrapped before a rule change must
    pick up the new rules on the next provision, and the guard must exist before the grant
    is ever usable, so it is applied first."""
    admission = k8s.AdmissionregistrationV1Api(api)
    guarded = ["namespaces", "resourcequotas", "limitranges"]
    policy = k8s.V1ValidatingAdmissionPolicy(
        metadata=k8s.V1ObjectMeta(name=DEPLOYER_NAMESPACE_GUARD),
        spec=k8s.V1ValidatingAdmissionPolicySpec(
            failure_policy="Fail",
            match_constraints=k8s.V1MatchResources(
                resource_rules=[
                    k8s.V1NamedRuleWithOperations(
                        api_groups=[""],
                        api_versions=["v1"],
                        operations=["CREATE", "UPDATE", "DELETE"],
                        resources=guarded,
                    )
                ]
            ),
            match_conditions=[
                k8s.V1MatchCondition(
                    name="is-launchpad-deployer",
                    expression=f"'{DEPLOYER_GROUP}' in request.userInfo.groups",
                )
            ],
            validations=[
                k8s.V1Validation(
                    expression=(
                        "request.resource.resource == 'namespaces'"
                        f" ? (object != null ? object : oldObject).metadata.name.startsWith('{APP_NAMESPACE_PREFIX}')"
                        f" : request.namespace.startsWith('{APP_NAMESPACE_PREFIX}')"
                    ),
                    message=f"launchpad deployers may only manage {APP_NAMESPACE_PREFIX}* namespaces",
                )
            ],
        ),
    )
    _create_or_replace(
        lambda: admission.create_validating_admission_policy(policy),
        lambda: admission.read_validating_admission_policy(DEPLOYER_NAMESPACE_GUARD),
        lambda: admission.replace_validating_admission_policy(DEPLOYER_NAMESPACE_GUARD, policy),
        policy,
        f"ValidatingAdmissionPolicy/{DEPLOYER_NAMESPACE_GUARD}",
        lines,
    )
    policy_binding = k8s.V1ValidatingAdmissionPolicyBinding(
        metadata=k8s.V1ObjectMeta(name=DEPLOYER_NAMESPACE_GUARD),
        spec=k8s.V1ValidatingAdmissionPolicyBindingSpec(
            policy_name=DEPLOYER_NAMESPACE_GUARD, validation_actions=["Deny"],
        ),
    )
    _create_or_replace(
        lambda: admission.create_validating_admission_policy_binding(policy_binding),
        lambda: admission.read_validating_admission_policy_binding(DEPLOYER_NAMESPACE_GUARD),
        lambda: admission.replace_validating_admission_policy_binding(DEPLOYER_NAMESPACE_GUARD, policy_binding),
        policy_binding,
        f"ValidatingAdmissionPolicyBinding/{DEPLOYER_NAMESPACE_GUARD}",
        lines,
    )

    rbac = k8s.RbacAuthorizationV1Api(api)
    cluster_role = k8s.V1ClusterRole(
        metadata=k8s.V1ObjectMeta(name=NAMESPACE_CREATOR_CLUSTER_ROLE),
        rules=[
            k8s.V1PolicyRule(api_groups=[""], resources=["namespaces"], verbs=["get", "create", "delete"]),
            k8s.V1PolicyRule(api_groups=[""], resources=["resourcequotas", "limitranges"], verbs=["get", "create"]),
        ],
    )
    _create_or_replace(
        lambda: rbac.create_cluster_role(cluster_role),
        lambda: rbac.read_cluster_role(NAMESPACE_CREATOR_CLUSTER_ROLE),
        lambda: rbac.replace_cluster_role(NAMESPACE_CREATOR_CLUSTER_ROLE, cluster_role),
        cluster_role,
        f"ClusterRole/{NAMESPACE_CREATOR_CLUSTER_ROLE}",
        lines,
    )
    binding = k8s.V1ClusterRoleBinding(
        metadata=k8s.V1ObjectMeta(name=NAMESPACE_CREATOR_CLUSTER_ROLE),
        role_ref=k8s.V1RoleRef(api_group="rbac.authorization.k8s.io", kind="ClusterRole", name=NAMESPACE_CREATOR_CLUSTER_ROLE),
        subjects=[k8s.RbacV1Subject(kind="Group", name=DEPLOYER_GROUP, api_group="rbac.authorization.k8s.io")],
    )
    _get_or_create(
        lambda: rbac.create_cluster_role_binding(binding),
        f"ClusterRoleBinding/{NAMESPACE_CREATOR_CLUSTER_ROLE}",
        lines,
    )


def _create_or_replace(create, read, replace, body, kind: str, lines: list):
    """On a rerun the object exists, so replace it with the current rules. A replace must
    carry the live object's resourceVersion: the API server rejects an update without one
    for some kinds (ValidatingAdmissionPolicy: 422 "metadata.resourceVersion: must be
    specified for an update"), which failed every reprovision of an existing EKS infra."""
    try:
        create()
        lines.append(f"[k8s] created {kind}")
    except ApiException as e:
        if e.status != 409:
            raise
        body.metadata.resource_version = read().metadata.resource_version
        replace()
        lines.append(f"[k8s] replaced {kind}")


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
    networking = k8s.NetworkingV1Api(api)
    ingress = k8s.V1Ingress(
        metadata=k8s.V1ObjectMeta(
            name="bootstrap",
            annotations={
                # HTTP:80 only at bootstrap time — no certificate exists yet (this runs
                # before any infra has ever requested one), and an ALB HTTPS listener
                # cannot be created without one (a hard CreateListener constraint, not a
                # controller choice). apply_eks_tls patches this same annotation to add
                # HTTPS:443 once a certificate actually exists — see its docstring for why
                # that patch lives there and not here.
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}]',
                # H6: an empty-backend Service (a ClusterIP with no pods ever behind it)
                # used to sit here as the default action, so unmatched :443/:80 traffic hit
                # a target group with zero healthy targets and the ALB answered with a raw
                # 503 — indistinguishable from a real outage. A fixed-response action needs
                # no backing Service at all: the controller resolves the action by this
                # annotation's key before it ever looks up a Service/Endpoints object, so
                # this always returns a clean 404 regardless of cluster state. Unverified
                # against a real ALB controller — see plan/REAL-AWS-VALIDATION.md's H6 entry.
                f"alb.ingress.kubernetes.io/actions.{DEFAULT_BACKEND_SERVICE}": (
                    '{"type":"fixed-response","fixedResponseConfig":'
                    '{"contentType":"text/plain","statusCode":"404","messageBody":"Not Found"}}'
                ),
            },
        ),
        spec=k8s.V1IngressSpec(
            ingress_class_name=INGRESS_CLASS_NAME,
            default_backend=k8s.V1IngressBackend(
                service=k8s.V1IngressServiceBackend(
                    # "use-annotation" is the ALB controller's documented sentinel port
                    # name telling it to resolve the backend from the actions.<name>
                    # annotation above instead of a real Service port.
                    name=DEFAULT_BACKEND_SERVICE,
                    port=k8s.V1ServiceBackendPort(name="use-annotation"),
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
