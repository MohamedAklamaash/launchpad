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
                 listener_rules: dict, infra_id: str | None = None):
        self._service = service
        self._region = region
        self._account_id = account_id
        self._infra_id = infra_id
        self._deleted_services = deleted_services
        self._listener_rules = listener_rules
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
            descriptions.append(
                {
                    "serviceName": name,
                    "serviceArn": self._arn(f"service/{name}"),
                    "status": "INACTIVE" if deleted else "ACTIVE",
                    "runningCount": 0 if deleted else 1,
                    "desiredCount": 0 if deleted else 1,
                    "deployments": [
                        {"status": "PRIMARY", "rolloutState": "COMPLETED", "failedTasks": 0}
                    ],
                }
            )
        return {"services": descriptions}

    def create_service(self, **kwargs):
        name = kwargs.get("serviceName", "app-service")
        return {"service": {"serviceArn": self._arn(f"service/{name}"), "serviceName": name}}

    def update_service(self, **kwargs):
        service = kwargs.get("service", "app-service")
        return {"service": {"serviceArn": self._arn(f"service/{service}"), "serviceName": service}}

    def delete_service(self, **kwargs):
        service = str(kwargs.get("service", "")).split("/")[-1]
        if service:
            self._deleted_services.add(service)
        return {"service": {"status": "DRAINING"}}

    def create_target_group(self, **kwargs):
        name = kwargs.get("Name", "tg")
        return {
            "TargetGroups": [
                {
                    "TargetGroupArn": self._arn(f"targetgroup/{name}/{_suffix(name)}"),
                    "TargetGroupName": name,
                    "VpcId": kwargs.get("VpcId", self._mock_vpc_id),
                }
            ]
        }

    def describe_target_groups(self, **kwargs):
        arns = kwargs.get("TargetGroupArns") or []
        names = kwargs.get("Names") or []
        groups = []
        for arn in arns:
            groups.append({"TargetGroupArn": arn, "VpcId": self._mock_vpc_id})
        for name in names:
            groups.append(
                {
                    "TargetGroupArn": self._arn(f"targetgroup/{name}/{_suffix(name)}"),
                    "TargetGroupName": name,
                    "VpcId": self._mock_vpc_id,
                }
            )
        return {"TargetGroups": groups}

    def delete_target_group(self, **kwargs):
        return {}

    def describe_listeners(self, **kwargs):
        lb_arn = kwargs.get("LoadBalancerArn", "alb")
        return {"Listeners": [{"ListenerArn": self._arn(f"listener/app/{_suffix(lb_arn)}/80")}]}

    def describe_rules(self, **kwargs):
        # Reflect rules created via create_rule so verify_target_group_attached and
        # get_next_priority see the forward action they just wired up — a stateless
        # mock would loop forever on "target group not in listener rules yet".
        listener_arn = kwargs.get("ListenerArn", "listener")
        default = {"RuleArn": self._arn("listener-rule/default"), "Priority": "default", "Actions": []}
        return {"Rules": self._listener_rules.get(listener_arn, []) + [default]}

    def create_rule(self, **kwargs):
        listener_arn = kwargs.get("ListenerArn", "listener")
        rule = {
            "RuleArn": self._arn(f"listener-rule/{_suffix(str(kwargs))}"),
            "Priority": str(kwargs.get("Priority", 1)),
            "Actions": kwargs.get("Actions", []),
        }
        self._listener_rules.setdefault(listener_arn, []).append(rule)
        return {"Rules": [rule]}

    def delete_rule(self, **kwargs):
        rule_arn = kwargs.get("RuleArn")
        for rules in self._listener_rules.values():
            rules[:] = [r for r in rules if r["RuleArn"] != rule_arn]
        return {}

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

    def create_log_group(self, **kwargs):
        return {}

    def describe_subnets(self, **kwargs):
        return {
            "Subnets": [
                {"SubnetId": _hex_resource_id("subnet", f"{self._account_id}-a")},
                {"SubnetId": _hex_resource_id("subnet", f"{self._account_id}-b")},
            ]
        }

    def describe_security_groups(self, **kwargs):
        return {"SecurityGroups": []}

    def create_security_group(self, **kwargs):
        name = kwargs.get("GroupName", "sg")
        return {"GroupId": _hex_resource_id("sg", name)}

    def authorize_security_group_ingress(self, **kwargs):
        return {}

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

    def client(self, service_name: str, **kwargs):
        return MockClient(
            service_name, self.region_name, self._account_id,
            self._deleted_services, self._listener_rules, infra_id=self._infra_id,
        )
