"""SNI certificate attach/detach and rule deletion on the shared 443 listener — F1b part
3b custom domains. A custom domain's certificate is attached to the listener directly via
boto3 (never Terraform, which doesn't track a resource set that changes on every claim),
counted against the ALB's own SNI cap, and idempotent on both attach and detach so
teardown never wedges on a resource that's already gone."""
import pytest
from aws.alb import ALBClient, SniCertificateCapExceeded
from botocore.exceptions import ClientError as _ClientErrorBase


class _ClientError(_ClientErrorBase):
    def __init__(self, code):
        super().__init__({"Error": {"Code": code}}, "MockOperation")


class _FakeElbv2:
    def __init__(self, cert_cap=25):
        self.rules = {}
        self.certificates = []
        self.cert_cap = cert_cap
        self._next_priority = 1

    def describe_rules(self, **kwargs):
        return {"Rules": [
            {"Priority": str(r["priority"]), "RuleArn": arn, "Conditions": r["conditions"]}
            for arn, r in self.rules.items()
        ]}

    def create_rule(self, **kwargs):
        priority = kwargs["Priority"]
        arn = f"arn:aws:elasticloadbalancing:::listener-rule/{priority}"
        self.rules[arn] = {"priority": priority, "conditions": kwargs["Conditions"], "actions": kwargs["Actions"]}
        return {"Rules": [{"RuleArn": arn}]}

    def delete_rule(self, RuleArn):
        if RuleArn not in self.rules:
            raise _ClientError("RuleNotFound")
        del self.rules[RuleArn]

    def describe_listener_certificates(self, ListenerArn, Marker=None):
        return {"Certificates": list(self.certificates)}

    def add_listener_certificates(self, ListenerArn, Certificates):
        non_default = [c for c in self.certificates if not c.get("IsDefault")]
        if len(non_default) >= self.cert_cap:
            raise _ClientError("TooManyCertificates")
        for c in Certificates:
            if not any(existing["CertificateArn"] == c["CertificateArn"] for existing in self.certificates):
                self.certificates.append({"CertificateArn": c["CertificateArn"], "IsDefault": False})

    def remove_listener_certificates(self, ListenerArn, Certificates):
        arns = {c["CertificateArn"] for c in Certificates}
        self.certificates = [c for c in self.certificates if c["CertificateArn"] not in arns]

    class exceptions:
        ClientError = _ClientError

        class PriorityInUseException(Exception):
            pass


class _FakeSession:
    def __init__(self, cert_cap=25):
        self.elbv2 = _FakeElbv2(cert_cap=cert_cap)

    def client(self, name):
        assert name == "elbv2"
        return self.elbv2


def _client(cert_cap=25):
    session = _FakeSession(cert_cap=cert_cap)
    return ALBClient(session), session.elbv2


def test_delete_rule_removes_it():
    client, elbv2 = _client()
    arn = client.create_host_forward_rule("listener-arn", "tg-arn", "app.example.com")

    client.delete_rule(arn)

    assert arn not in elbv2.rules


def test_delete_rule_is_idempotent_on_already_gone_rule():
    client, _elbv2 = _client()
    client.delete_rule("arn:aws:elasticloadbalancing:::listener-rule/does-not-exist")


def test_add_listener_certificate_then_counted():
    client, _elbv2 = _client()

    client.add_listener_certificate("listener-arn", "arn:aws:acm:x:1:certificate/a")

    assert client.count_listener_certificates("listener-arn") == 1
    assert client.has_listener_certificate("listener-arn", "arn:aws:acm:x:1:certificate/a") is True


def test_default_certificate_excluded_from_count():
    client, elbv2 = _client()
    elbv2.certificates.append({"CertificateArn": "arn:default", "IsDefault": True})

    assert client.count_listener_certificates("listener-arn") == 0


def test_add_listener_certificate_is_idempotent():
    client, _elbv2 = _client()

    client.add_listener_certificate("listener-arn", "arn:a")
    client.add_listener_certificate("listener-arn", "arn:a")

    assert client.count_listener_certificates("listener-arn") == 1


def test_add_listener_certificate_raises_cap_exceeded():
    client, _elbv2 = _client(cert_cap=1)
    client.add_listener_certificate("listener-arn", "arn:a")

    with pytest.raises(SniCertificateCapExceeded):
        client.add_listener_certificate("listener-arn", "arn:b")


def test_remove_listener_certificate_removes_it():
    client, _elbv2 = _client()
    client.add_listener_certificate("listener-arn", "arn:a")

    client.remove_listener_certificate("listener-arn", "arn:a")

    assert client.count_listener_certificates("listener-arn") == 0


def test_remove_listener_certificate_is_idempotent_when_never_attached():
    client, _elbv2 = _client()
    client.remove_listener_certificate("listener-arn", "arn:never-attached")


def test_remove_listener_certificate_swallows_listener_gone():
    client, elbv2 = _client()

    def _raise(ListenerArn, Certificates):
        raise _ClientError("ListenerNotFound")

    elbv2.remove_listener_certificates = _raise
    client.remove_listener_certificate("listener-arn", "arn:a")
