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
        return {"Rules": [
            {"Priority": str(r["priority"]), "RuleArn": r["arn"], "Conditions": r["conditions"]}
            for r in self.rules
        ]}

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

    def set_rule_priorities(self, **kwargs):
        by_arn = {p["RuleArn"]: p["Priority"] for p in kwargs["RulePriorities"]}
        for rule in self.rules:
            if rule["arn"] in by_arn:
                rule["priority"] = by_arn[rule["arn"]]
        return {"Rules": [{"RuleArn": arn} for arn in by_arn]}

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


# ── ensure_host_redirect_rule (F1b part 3a) ─────────────────────────────────────────────

def test_ensure_host_redirect_rule_creates_a_wildcard_rule_at_priority_1():
    client, elbv2 = _client()

    arn = client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")

    assert len(elbv2.rules) == 1
    rule = elbv2.rules[0]
    assert rule["arn"] == arn
    assert rule["priority"] == 1
    assert rule["conditions"] == [{"Field": "host-header", "Values": ["*.abc123.launchpad.app"]}]
    assert rule["actions"][0]["Type"] == "redirect"
    assert rule["actions"][0]["RedirectConfig"]["Protocol"] == "HTTPS"


def test_ensure_host_redirect_rule_is_idempotent():
    client, elbv2 = _client()

    first = client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")
    second = client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")

    assert first == second
    assert len(elbv2.rules) == 1


def test_ensure_host_redirect_rule_displaces_an_existing_rule_at_priority_1():
    """A per-app path rule already occupies priority 1 (the general allocator starts at 1)
    — the wildcard redirect must still end up at priority 1, and the displaced rule must
    end up at a distinct, non-colliding priority, never at 1 alongside it."""
    client, elbv2 = _client()
    client._create_rule_with_retry(
        "listener-arn",
        conditions=[{"Field": "path-pattern", "Values": ["/existing*"]}],
        actions=[{"Type": "forward", "TargetGroupArn": "tg-arn"}],
    )

    redirect_arn = client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")

    priorities = {r["arn"]: r["priority"] for r in elbv2.rules}
    assert priorities[redirect_arn] == 1
    other_arn = next(arn for arn in priorities if arn != redirect_arn)
    assert priorities[other_arn] != 1
    assert len({priorities[redirect_arn], priorities[other_arn]}) == 2


def test_ensure_host_redirect_rule_never_forwards():
    client, elbv2 = _client()

    client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")

    assert all(rule["actions"][0]["Type"] == "redirect" for rule in elbv2.rules)


# ── R1: lookup must re-claim priority 1, not just creation ─────────────────────────────

def test_ensure_host_redirect_rule_reclaims_priority_1_when_found_outranked():
    """A previous set_rule_priorities call that never ran (or failed partway) leaves the
    redirect rule already existing but not at priority 1. The next call to
    ensure_host_redirect_rule — from any later deploy — must notice and fix this, not just
    return the ARN as if everything were fine."""
    client, elbv2 = _client()

    # Simulate the broken state directly: a redirect rule exists, parked at priority 5,
    # with something else now sitting at priority 1.
    elbv2.rules.append({
        "priority": 5, "arn": "arn:aws:elasticloadbalancing:::listener-rule/redirect",
        "conditions": [{"Field": "host-header", "Values": ["*.abc123.launchpad.app"]}],
        "actions": [{"Type": "redirect", "RedirectConfig": {"Protocol": "HTTPS"}}],
    })
    elbv2.rules.append({
        "priority": 1, "arn": "arn:aws:elasticloadbalancing:::listener-rule/path",
        "conditions": [{"Field": "path-pattern", "Values": ["/other"]}],
        "actions": [{"Type": "forward", "TargetGroupArn": "tg-other"}],
    })

    returned_arn = client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")

    assert returned_arn == "arn:aws:elasticloadbalancing:::listener-rule/redirect"
    priorities = {r["arn"]: r["priority"] for r in elbv2.rules}
    assert priorities["arn:aws:elasticloadbalancing:::listener-rule/redirect"] == 1
    assert priorities["arn:aws:elasticloadbalancing:::listener-rule/path"] != 1


def test_ensure_host_redirect_rule_is_a_noop_when_already_at_priority_1():
    client, elbv2 = _client()
    client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")
    set_priorities_calls_before = 0

    def _counting_set_rule_priorities(**kwargs):
        nonlocal set_priorities_calls_before
        set_priorities_calls_before += 1
        return elbv2.__class__.set_rule_priorities(elbv2, **kwargs)

    elbv2.set_rule_priorities = _counting_set_rule_priorities

    client.ensure_host_redirect_rule("listener-arn", "abc123", "launchpad.app")

    assert set_priorities_calls_before == 0


# ── R1: path conditions no longer overlap between different slugs ──────────────────────

def test_create_listener_rule_uses_exact_and_prefixed_conditions_not_a_glob(monkeypatch):
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    client, elbv2 = _client()

    client.create_listener_rule("listener-arn", "tg-arn", ["/a", "/a/*"], priority=1)

    condition = elbv2.rules[0]["conditions"][0]
    assert condition == {"Field": "path-pattern", "Values": ["/a", "/a/*"]}


def test_create_listener_rule_still_accepts_a_single_string_for_backward_compatibility(monkeypatch):
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    client, elbv2 = _client()

    client.create_listener_rule("listener-arn", "tg-arn", "/legacy*", priority=1)

    assert elbv2.rules[0]["conditions"][0]["Values"] == ["/legacy*"]


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
