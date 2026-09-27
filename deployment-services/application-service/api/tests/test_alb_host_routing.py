"""Host-header routing rules and the health-check-path lockstep — F1b part 2 pre-review §5.

create_host_forward_rule (443, forward) and create_host_redirect_rule (:80, redirect-only,
never forward) share create_listener_rule's priority-lock/retry machinery; modify_target_group
is the existing-TG counterpart to create_target_group's health_check_path parameter.
"""
from aws.alb import ALBClient


class _FakeElbv2:
    def __init__(self):
        self.rules = []
        self.modified = []
        self._next_priority = 1

    def describe_rules(self, **kwargs):
        return {"Rules": [{"Priority": str(r["priority"]), "RuleArn": r["arn"]} for r in self.rules]}

    def create_rule(self, **kwargs):
        priority = kwargs["Priority"]
        arn = f"arn:aws:elasticloadbalancing:::listener-rule/{priority}"
        self.rules.append({
            "priority": priority, "arn": arn,
            "conditions": kwargs["Conditions"], "actions": kwargs["Actions"],
        })
        return {"Rules": [{"RuleArn": arn}]}

    def modify_target_group(self, **kwargs):
        self.modified.append(kwargs)
        return {"TargetGroups": [{"TargetGroupArn": kwargs["TargetGroupArn"]}]}

    class exceptions:
        class PriorityInUseException(Exception):
            pass


class _FakeSession:
    def __init__(self):
        self.elbv2 = _FakeElbv2()

    def client(self, name):
        assert name == "elbv2"
        return self.elbv2


def _client():
    session = _FakeSession()
    return ALBClient(session), session.elbv2


def test_host_forward_rule_forwards_to_the_target_group():
    client, elbv2 = _client()

    client.create_host_forward_rule("listener-arn", "tg-arn", "myapp.abc123.launchpad.app")

    assert len(elbv2.rules) == 1
    rule = elbv2.rules[0]
    assert rule["conditions"] == [{"Field": "host-header", "Values": ["myapp.abc123.launchpad.app"]}]
    assert rule["actions"] == [{"Type": "forward", "TargetGroupArn": "tg-arn"}]


def test_host_redirect_rule_never_forwards():
    client, elbv2 = _client()

    client.create_host_redirect_rule("listener-arn", "myapp.abc123.launchpad.app")

    assert len(elbv2.rules) == 1
    rule = elbv2.rules[0]
    assert rule["conditions"] == [{"Field": "host-header", "Values": ["myapp.abc123.launchpad.app"]}]
    action = rule["actions"][0]
    assert action["Type"] == "redirect"
    assert action["RedirectConfig"]["Protocol"] == "HTTPS"
    assert action["RedirectConfig"]["Port"] == "443"
    assert action["RedirectConfig"]["StatusCode"] == "HTTP_301"


def test_host_forward_and_redirect_rules_get_distinct_priorities_on_the_same_listener():
    client, elbv2 = _client()

    client.create_host_forward_rule("listener-arn", "tg-arn", "a.abc.launchpad.app")
    client.create_host_redirect_rule("listener-arn", "a.abc.launchpad.app")

    priorities = {r["priority"] for r in elbv2.rules}
    assert len(priorities) == 2


def test_modify_target_group_updates_health_check_path():
    client, elbv2 = _client()

    client.modify_target_group("tg-arn", "/_lp_health")

    assert elbv2.modified == [{"TargetGroupArn": "tg-arn", "HealthCheckPath": "/_lp_health"}]


def test_create_target_group_accepts_a_custom_health_check_path():
    class _FakeElbv2WithCreate(_FakeElbv2):
        def create_target_group(self, **kwargs):
            self.last_create = kwargs
            return {"TargetGroups": [{"TargetGroupArn": "tg-arn", "TargetGroupName": kwargs["Name"], "VpcId": kwargs["VpcId"]}]}

    session = _FakeSession()
    session.elbv2 = _FakeElbv2WithCreate()
    client = ALBClient(session)

    client.create_target_group("tg", "vpc-1", health_check_path="/_lp_health")

    assert session.elbv2.last_create["HealthCheckPath"] == "/_lp_health"


def test_create_target_group_defaults_health_check_path_to_root():
    class _FakeElbv2WithCreate(_FakeElbv2):
        def create_target_group(self, **kwargs):
            self.last_create = kwargs
            return {"TargetGroups": [{"TargetGroupArn": "tg-arn", "TargetGroupName": kwargs["Name"], "VpcId": kwargs["VpcId"]}]}

    session = _FakeSession()
    session.elbv2 = _FakeElbv2WithCreate()
    client = ALBClient(session)

    client.create_target_group("tg", "vpc-1")

    assert session.elbv2.last_create["HealthCheckPath"] == "/"
