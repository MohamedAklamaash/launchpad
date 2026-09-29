import hashlib

from botocore.exceptions import ClientError

DEFAULT_REGION = "us-west-2"
MOCK_ACCOUNT_ID = "000000000000"


def _suffix(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()[:8]


def _hex_resource_id(prefix: str, seed: str) -> str:
    return f"{prefix}-{hashlib.md5(seed.encode()).hexdigest()[:17]}"


def _hex_infra_id(prefix: str, infra_id: str, salt: str = "") -> str:
    # Mirror infrastructure-service api/mock/aws_fixtures.py::_hex_id so the VpcId the deploy reads
    # back for a target group matches the environment.vpc_id the provisioner recorded — otherwise
    # every redeploy sees a VPC mismatch and needlessly deletes + recreates the target group.
    return f"{prefix}-{hashlib.md5(f'{infra_id}:{salt}'.encode()).hexdigest()[:17]}"


_MOCK_RESOLVED_SHA = "0" * 39 + "1"


def mock_eks_alb_dns(infra_id: str, region: str) -> str:
    """The mock's own deterministic stand-in for the shared EKS group ALB's DNS name — see
    MockClient.describe_load_balancers. Test fixtures that want EKSDeployer's B1 live check
    (find the group ALB from Environment.alb_dns) to succeed against the mock must set
    Environment.alb_dns to exactly this value; anything else (e.g. a fixed literal string
    like "alb.example.com") makes the live check correctly fail closed to path mode, since
    that is exactly what it's supposed to do when the ALB can't be found."""
    return f"mock-eks-alb-{_suffix(str(infra_id))}.{region}.elb.amazonaws.com"


def mock_eks_alb_arn(infra_id: str, region: str, account_id: str) -> str:
    return f"arn:aws:elasticloadbalancing:{region}:{account_id}:loadbalancer/app/mock-eks-alb/{_suffix(str(infra_id))}"


class _MockClientExceptions:
    def __init__(self, service: str):
        self._service = service
        self._cache: dict = {}

    def __getattr__(self, name: str):
        if name not in self._cache:
            self._cache[name] = type(name, (ClientError,), {"__init__": _benign_client_error_init})
        return self._cache[name]


def _benign_client_error_init(self, *args, **kwargs):
    Exception.__init__(self, *args)


class _MockMeta:
    def __init__(self, region: str):
        self.region_name = region


class _MockPaginator:
    def paginate(self, **kwargs):
        return iter(())


class MockClient:
    def __init__(self, service: str, region: str, account_id: str, deleted_services: set,
                 listener_rules: dict, infra_id: str | None = None, listener_certificates: dict | None = None,
                 target_groups: dict | None = None, service_deployments: dict | None = None):
        self._service = service
        self._region = region
        self._account_id = account_id
        self._infra_id = infra_id
        self._deleted_services = deleted_services
        self._listener_rules = listener_rules
        self._listener_certificates = listener_certificates if listener_certificates is not None else {}
        # H4: name -> {"arn", "vpc_id", "tags"} — shared across every MockClient built
        # from the same MockSession, so a second create_target_group call for the same
        # name (a redeploy, or a test simulating a foreign/orphaned target group) sees
        # what a real elbv2 CreateTargetGroup would: DuplicateTargetGroupNameException,
        # never a silent second success.
        self._target_groups = target_groups if target_groups is not None else {}
        # service name -> deployments list, shared across every MockClient built from the
        # same MockSession. Defaults to a single converged PRIMARY (see describe_services)
        # unless a test overrides this to express an in-progress or stuck rollout — e.g. a
        # PRIMARY that never converges next to an ACTIVE deployment still running the old
        # task, the shape wait_for_service_stable (aws/ecs.py) must fail on rather than
        # report stable (see the e2e-web incident it fixes).
        self._service_deployments = service_deployments if service_deployments is not None else {}
        self.meta = _MockMeta(region)
        self.exceptions = _MockClientExceptions(service)

    def get_paginator(self, _name: str):
        return _MockPaginator()

    def _arn(self, resource: str) -> str:
        return f"arn:aws:{self._service}:{self._region}:{self._account_id}:{resource}"

    def register_task_definition(self, **kwargs):
        family = kwargs.get("family", "app")
        return {"taskDefinition": {"taskDefinitionArn": self._arn(f"task-definition/{family}:1")}}

    def deregister_task_definition(self, **kwargs):
        return {"taskDefinition": {"taskDefinitionArn": kwargs.get("taskDefinition", "")}}

    def describe_services(self, **kwargs):
        services = kwargs.get("services", [])
        descriptions = []
        for service in services:
            name = str(service).split("/")[-1]
            deleted = name in self._deleted_services
            running = 0 if deleted else 1
            desired = 0 if deleted else 1
            deployments = self._service_deployments.get(name) if not deleted else None
            if deployments is None:
                deployments = [
                    {
                        "status": "PRIMARY", "rolloutState": "COMPLETED", "failedTasks": 0,
                        "runningCount": running, "desiredCount": desired,
                    }
                ]
            descriptions.append(
                {
                    "serviceName": name,
                    "serviceArn": self._arn(f"service/{name}"),
                    "status": "INACTIVE" if deleted else "ACTIVE",
                    "runningCount": running,
                    "desiredCount": desired,
                    "deployments": deployments,
                }
            )
        return {"services": descriptions}

    def create_service(self, **kwargs):
        name = kwargs.get("serviceName", "app-service")
        # Real ECS CreateService validates every `loadBalancers` entry: a target group
        # with no associated load balancer yet raises InvalidParameterException, and it
        # is never health-checked either way — see
        # ApplicationDeploymentService._create_ecs_service_with_routing, the fix this
        # enforces against.
        for lb in kwargs.get("loadBalancers") or []:
            tg_arn = lb.get("targetGroupArn")
            if tg_arn and not self._target_group_load_balancer_arns(tg_arn):
                raise self.exceptions.InvalidParameterException(
                    f"The target group with targetGroupArn {tg_arn} does not have an "
                    "associated load balancer"
                )
        # A create after a delete (the rollback recreate path) is a fresh, ACTIVE
        # service — it must stop showing up as deleted to describe_services/update_service.
        self._deleted_services.discard(name)
        return {"service": {"serviceArn": self._arn(f"service/{name}"), "serviceName": name}}

    def update_service(self, **kwargs):
        service = str(kwargs.get("service", "app-service")).split("/")[-1]
        if service in self._deleted_services:
            # Real ECS: UpdateService on a service that's been deleted (or never
            # existed) raises ServiceNotFoundException — a rollback whose target service
            # was removed (e.g. ApplicationRetryDeployView's cleanup job) must see this.
            raise self.exceptions.ServiceNotFoundException(
                f"Service not found: {service}"
            )
        return {"service": {"serviceArn": self._arn(f"service/{service}"), "serviceName": service}}

    def delete_service(self, **kwargs):
        service = str(kwargs.get("service", "")).split("/")[-1]
        if service:
            self._deleted_services.add(service)
        return {"service": {"status": "DRAINING"}}

    def create_target_group(self, **kwargs):
        name = kwargs.get("Name", "tg")
        if name in self._target_groups:
            # Real elbv2 behaviour this mock exists to reproduce (H4): CreateTargetGroup
            # on a name that already exists raises, regardless of whether the caller's
            # settings match — ALBClient.create_target_group's DuplicateTargetGroupNameException
            # handler is what decides whether to adopt it.
            raise self.exceptions.DuplicateTargetGroupNameException(
                f"A target group with the same name '{name}' already exists"
            )
        vpc_id = kwargs.get("VpcId", self._mock_vpc_id)
        tags = {t["Key"]: t["Value"] for t in kwargs.get("Tags", [])}
        arn = self._arn(f"targetgroup/{name}/{_suffix(name)}")
        self._target_groups[name] = {"arn": arn, "vpc_id": vpc_id, "tags": tags}
        return {
            "TargetGroups": [
                {
                    "TargetGroupArn": arn,
                    "TargetGroupName": name,
                    "VpcId": vpc_id,
                }
            ]
        }

    def describe_target_groups(self, **kwargs):
        arns = kwargs.get("TargetGroupArns") or []
        names = kwargs.get("Names") or []
        groups = []
        for arn in arns:
            groups.append({
                "TargetGroupArn": arn, "VpcId": self._mock_vpc_id,
                "LoadBalancerArns": self._target_group_load_balancer_arns(arn),
            })
        for name in names:
            existing = self._target_groups.get(name)
            if existing:
                groups.append({
                    "TargetGroupArn": existing["arn"], "TargetGroupName": name,
                    "VpcId": existing["vpc_id"],
                    "LoadBalancerArns": self._target_group_load_balancer_arns(existing["arn"]),
                })
            else:
                groups.append(
                    {
                        "TargetGroupArn": self._arn(f"targetgroup/{name}/{_suffix(name)}"),
                        "TargetGroupName": name,
                        "VpcId": self._mock_vpc_id,
                        "LoadBalancerArns": [],
                    }
                )
        return {"TargetGroups": groups}

    def _target_group_load_balancer_arns(self, target_group_arn: str) -> list:
        """Real ALB populates a target group's LoadBalancerArns once some listener rule
        on that load balancer forwards to it — modelled here by scanning every rule this
        mock has created for a forward Action pointed at this target group, matching
        real DescribeTargetGroups without tracking an explicit attach/detach step."""
        for rules in self._listener_rules.values():
            for rule in rules:
                for action in rule.get("Actions", []):
                    if action.get("Type") == "forward" and action.get("TargetGroupArn") == target_group_arn:
                        return [self._arn("loadbalancer/app/mock-alb/attached")]
        return []

    def describe_tags(self, **kwargs):
        arns = kwargs.get("ResourceArns") or []
        by_arn = {tg["arn"]: tg["tags"] for tg in self._target_groups.values()}
        return {
            "TagDescriptions": [
                {"ResourceArn": arn, "Tags": [{"Key": k, "Value": v} for k, v in by_arn.get(arn, {}).items()]}
                for arn in arns
            ]
        }

    def delete_target_group(self, **kwargs):
        arn = kwargs.get("TargetGroupArn")
        stale = [name for name, tg in self._target_groups.items() if tg["arn"] == arn]
        for name in stale:
            del self._target_groups[name]
        return {}

    def modify_target_group(self, **kwargs):
        return {"TargetGroups": [{"TargetGroupArn": kwargs.get("TargetGroupArn", "")}]}

    def describe_load_balancers(self, **kwargs):
        # Real elbv2 has no way to filter DescribeLoadBalancers by DNS name (only by ARN
        # or LB name) — the real EKSDeployer._find_group_alb_arn call this backs paginates
        # ALL load balancers and matches DNSName client-side. The mock models exactly one
        # LB (see mock_eks_alb_dns/mock_eks_alb_arn above), so a Marker/NextMarker page
        # request always ends the scan immediately, and it's up to the caller/fixture to
        # have set Environment.alb_dns to the matching mock DNS name if it wants the B1
        # live check to succeed.
        if kwargs.get("Marker") or not self._infra_id:
            return {"LoadBalancers": []}
        return {"LoadBalancers": [{
            "LoadBalancerArn": mock_eks_alb_arn(self._infra_id, self._region, self._account_id),
            "DNSName": mock_eks_alb_dns(self._infra_id, self._region),
        }]}

    def describe_listeners(self, **kwargs):
        lb_arn = kwargs.get("LoadBalancerArn", "alb")
        # Port is load-bearing: get_listener_arn selects by it rather than taking the
        # first listener, so a stub without it would strand every mock-mode deploy on
        # "No listener found". Both :80 and :443 always exist in mock mode — real
        # terraform only creates :443 once a certificate is ISSUED (modules/alb,
        # enable_https), but mock provisioning never runs terraform at all (see
        # infrastructure-service's TerraformWorker._mock_provision), so there is no
        # equivalent "not yet applied" state to model here; the app-level tls_status/
        # dns_synced/https_ready gate (api/common/host_url.py) is what actually decides
        # whether a mock deploy attempts host mode.
        return {"Listeners": [
            {"ListenerArn": self._arn(f"listener/app/{_suffix(lb_arn)}/80"), "Port": 80},
            {"ListenerArn": self._arn(f"listener/app/{_suffix(lb_arn)}/443"), "Port": 443},
        ]}

    def describe_rules(self, **kwargs):
        # Reflect rules created via create_rule so verify_target_group_attached and
        # get_next_priority see the forward action they just wired up — a stateless
        # mock would loop forever on "target group not in listener rules yet". Conditions
        # are included so a caller (e.g. ensure_host_redirect_rule) can find an existing
        # rule by its host-header condition, the same as real ALB's describe_rules does.
        listener_arn = kwargs.get("ListenerArn", "listener")
        default = {"RuleArn": self._arn("listener-rule/default"), "Priority": "default", "Actions": [], "Conditions": []}
        return {"Rules": self._listener_rules.get(listener_arn, []) + [default]}

    def create_rule(self, **kwargs):
        listener_arn = kwargs.get("ListenerArn", "listener")
        rule = {
            "RuleArn": self._arn(f"listener-rule/{_suffix(str(kwargs))}"),
            "Priority": str(kwargs.get("Priority", 1)),
            "Actions": kwargs.get("Actions", []),
            "Conditions": kwargs.get("Conditions", []),
        }
        self._listener_rules.setdefault(listener_arn, []).append(rule)
        return {"Rules": [rule]}

    def delete_rule(self, **kwargs):
        rule_arn = kwargs.get("RuleArn")
        for rules in self._listener_rules.values():
            rules[:] = [r for r in rules if r["RuleArn"] != rule_arn]
        return {}

    def describe_listener_certificates(self, **kwargs):
        listener_arn = kwargs.get("ListenerArn", "listener")
        return {"Certificates": list(self._listener_certificates.get(listener_arn, []))}

    def add_listener_certificates(self, **kwargs):
        # Real ALB's SNI cap (25/listener) isn't modeled here — the mock never runs
        # 25+ custom-domain claims, and the cap-exceeded path is exercised against the
        # real ALBClient methods directly (test_alb_sni_certificates.py), not this mock.
        listener_arn = kwargs.get("ListenerArn", "listener")
        certs = self._listener_certificates.setdefault(listener_arn, [])
        for cert in kwargs.get("Certificates", []):
            if not any(c["CertificateArn"] == cert["CertificateArn"] for c in certs):
                certs.append({"CertificateArn": cert["CertificateArn"], "IsDefault": False})
        return {}

    def remove_listener_certificates(self, **kwargs):
        listener_arn = kwargs.get("ListenerArn", "listener")
        arns = {c["CertificateArn"] for c in kwargs.get("Certificates", [])}
        certs = self._listener_certificates.get(listener_arn, [])
        certs[:] = [c for c in certs if c["CertificateArn"] not in arns]
        return {}

    def set_rule_priorities(self, **kwargs):
        by_arn = {p["RuleArn"]: str(p["Priority"]) for p in kwargs.get("RulePriorities", [])}
        updated = []
        for rules in self._listener_rules.values():
            for rule in rules:
                if rule["RuleArn"] in by_arn:
                    rule["Priority"] = by_arn[rule["RuleArn"]]
                    updated.append(rule)
        return {"Rules": updated}

    def describe_target_health(self, **kwargs):
        return {"TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]}

    def batch_get_projects(self, **kwargs):
        return {"projects": [{"name": name} for name in kwargs.get("names", [])]}

    def create_project(self, **kwargs):
        return {"project": {"name": kwargs.get("name", "project")}}

    def update_project(self, **kwargs):
        return {"project": {"name": kwargs.get("name", "project")}}

    def start_build(self, **kwargs):
        project = kwargs.get("projectName", "build")
        return {"build": {"id": f"{project}:mock-{_suffix(project)}"}}

    def batch_get_builds(self, **kwargs):
        builds = []
        for build_id in kwargs.get("ids", []):
            builds.append(
                {
                    "id": build_id,
                    "buildStatus": "SUCCEEDED",
                    "currentPhase": "COMPLETED",
                    "logs": {},
                    # Real CodeBuild exports this from the buildspec. Modelled so mock
                    # deploys exercise the immutable-tag path instead of silently taking
                    # the -latest fallback meant for pre-existing projects.
                    "exportedEnvironmentVariables": [
                        {"name": "RESOLVED_SHA", "value": _MOCK_RESOLVED_SHA},
                    ],
                }
            )
        return {"builds": builds}

    def get_role(self, **kwargs):
        name = kwargs.get("RoleName", "role")
        return {"Role": {"RoleName": name, "Arn": f"arn:aws:iam::{self._account_id}:role/{name}"}}

    def create_role(self, **kwargs):
        name = kwargs.get("RoleName", "role")
        return {"Role": {"RoleName": name, "Arn": f"arn:aws:iam::{self._account_id}:role/{name}"}}

    def attach_role_policy(self, **kwargs):
        return {}

    def describe_images(self, **kwargs):
        # Mock images never expire — every tag or digest rollback might target is "found".
        # Tests for the expired-tag rejection exercise ECRClient.image_exists directly
        # against a fake client that raises ImageNotFoundException, the way real ECR would.
        # A deterministic digest (derived from the tag, not random) so a test can assert the
        # same digest was both recorded on a Deployment row and used to build an image ref.
        details = []
        for image_id in kwargs.get("imageIds") or []:
            tag, digest = image_id.get("imageTag"), image_id.get("imageDigest")
            if not tag and not digest:
                continue
            details.append({
                "imageTags": [tag] if tag else [],
                "imageDigest": digest or f"sha256:{hashlib.sha256(tag.encode()).hexdigest()}",
            })
        return {"imageDetails": details}

    def put_lifecycle_policy(self, **kwargs):
        return {
            "repositoryName": kwargs.get("repositoryName", "repo"),
            "lifecyclePolicyText": kwargs.get("lifecyclePolicyText", ""),
        }

    def create_log_group(self, **kwargs):
        return {}

    def list_tasks(self, **kwargs):
        # Deterministic per (cluster, service, desiredStatus) so isolation tests can
        # assert two different apps' task ids (and therefore log streams) never collide,
        # and so RUNNING/STOPPED each return a stable, distinct id across calls.
        cluster_name = str(kwargs.get("cluster", "cluster")).split("/")[-1]
        service_name = kwargs.get("serviceName", "")
        desired_status = kwargs.get("desiredStatus", "RUNNING")
        if not service_name or service_name in self._deleted_services:
            return {"taskArns": []}
        task_id = hashlib.md5(f"{cluster_name}:{service_name}:{desired_status}".encode()).hexdigest()
        return {"taskArns": [self._arn(f"task/{cluster_name}/{task_id}")]}

    def describe_tasks(self, **kwargs):
        from datetime import datetime, timedelta, timezone

        arns = kwargs.get("tasks", [])
        now = datetime.now(timezone.utc)
        # Later arns get an earlier stoppedAt, so a caller sorting by stoppedAt descending
        # sees a deterministic, non-input-order result — proving the sort actually runs.
        return {
            "tasks": [
                {"taskArn": arn, "stoppedAt": now - timedelta(seconds=index)}
                for index, arn in enumerate(arns)
            ]
        }

    def filter_log_events(self, **kwargs):
        streams = kwargs.get("logStreamNames") or []
        start_time = kwargs.get("startTime", 0)
        end_time = kwargs.get("endTime", 2**63 - 1)
        limit = kwargs.get("limit", 10000)
        events = []
        for stream in streams:
            base_ts = max(start_time, end_time - 2000)
            for offset, message in enumerate((
                f"[mock] {stream} started",
                # A GitGuardian-safe fake credential (not AWS-shaped) to prove the posture:
                # the customer's own application logs are shown back to them unredacted.
                "DATABASE_URL=postgres://app:mock-fake-password@db.internal:5432/app connected",
            )):
                # Clamp into [start_time, end_time] rather than just adding the offset:
                # a narrow or just-created-app window (endTime close to startTime) would
                # otherwise silently drop the later lines instead of returning them at
                # the boundary.
                ts = min(end_time, base_ts + offset)
                if start_time <= ts <= end_time:
                    events.append({"logStreamName": stream, "timestamp": ts, "message": message, "ingestionTime": ts})
        return {"events": events[:limit]}

    def describe_subnets(self, **kwargs):
        # Mirrors the terraform vpc module: two public subnets at cidrsubnet(vpc_cidr, 8, 0..1)
        # and two private at +2..3, tagged Type=public/private. CidrBlock is included because
        # the EKS ingress NetworkPolicy resolves the ALB's subnets through this call — a stub
        # without it would let the mock path silently skip that peer.
        subnets = [
            {"SubnetId": _hex_resource_id("subnet", f"{self._account_id}-pub-a"),
             "CidrBlock": "10.0.0.0/24", "Tags": [{"Key": "Type", "Value": "public"}]},
            {"SubnetId": _hex_resource_id("subnet", f"{self._account_id}-pub-b"),
             "CidrBlock": "10.0.1.0/24", "Tags": [{"Key": "Type", "Value": "public"}]},
            {"SubnetId": _hex_resource_id("subnet", f"{self._account_id}-priv-a"),
             "CidrBlock": "10.0.2.0/24", "Tags": [{"Key": "Type", "Value": "private"}]},
            {"SubnetId": _hex_resource_id("subnet", f"{self._account_id}-priv-b"),
             "CidrBlock": "10.0.3.0/24", "Tags": [{"Key": "Type", "Value": "private"}]},
        ]
        for f in kwargs.get("Filters", []):
            if f.get("Name") == "tag:Type":
                wanted = set(f.get("Values", []))
                subnets = [
                    s for s in subnets
                    if any(t["Key"] == "Type" and t["Value"] in wanted for t in s["Tags"])
                ]
        return {"Subnets": subnets}

    def describe_security_groups(self, **kwargs):
        return {"SecurityGroups": []}

    def create_security_group(self, **kwargs):
        name = kwargs.get("GroupName", "sg")
        return {"GroupId": _hex_resource_id("sg", name)}

    def authorize_security_group_ingress(self, **kwargs):
        return {}

    def describe_cluster(self, **kwargs):
        name = kwargs.get("name", "cluster")
        return {
            "cluster": {
                "name": name,
                "arn": self._arn(f"cluster/{name}"),
                "endpoint": f"https://{_suffix(name)}.mock.eks.{self._region}.amazonaws.com",
                # base64 of "mock-ca" — shared/k8s/client.py b64-decodes this on the real path.
                "certificateAuthority": {"data": "bW9jay1jYQ=="},
                "status": "ACTIVE",
            }
        }

    def assume_role(self, **kwargs):
        from datetime import datetime, timedelta, timezone

        return {
            "Credentials": {
                "AccessKeyId": f"ASIAMOCK{_suffix(self._account_id).upper()}",
                "SecretAccessKey": f"mock-secret-{_suffix(self._account_id)}",
                "SessionToken": f"mock-session-token-{_suffix(self._account_id)}",
                "Expiration": datetime.now(timezone.utc) + timedelta(hours=12),
            }
        }

    @property
    def _mock_vpc_id(self) -> str:
        if self._infra_id:
            return _hex_infra_id("vpc", self._infra_id, "vpc")
        return _hex_resource_id("vpc", self._account_id)

    def __getattr__(self, name: str):
        raise NotImplementedError(
            f"Mock AWS client does not implement {self._service}.{name}; "
            "add an explicit stub before routing this path through dev mode."
        )


class MockSession:
    def __init__(self, region: str, account_id: str, infra_id: str | None = None):
        self.region_name = region
        self._account_id = account_id
        self._infra_id = infra_id
        self._deleted_services: set = set()
        self._listener_rules: dict = {}
        self._listener_certificates: dict = {}
        self._target_groups: dict = {}
        # service name -> deployments list override — see MockClient.describe_services.
        self._service_deployments: dict = {}

    def client(self, service_name: str, **kwargs):
        return MockClient(
            service_name, self.region_name, self._account_id,
            self._deleted_services, self._listener_rules, infra_id=self._infra_id,
            listener_certificates=self._listener_certificates,
            target_groups=self._target_groups,
            service_deployments=self._service_deployments,
        )
