"""ALBClient.get_listener_arn must select by port, not by response order.

Per-app path rules are attached to whatever this returns. It used to take
describe_listeners()[0], which is correct only while an ALB has exactly one listener —
the condition that stops holding the moment HTTPS adds a :443 listener. A wrong answer
here does not fail loudly; it silently wires every app's routing rule onto the wrong
listener.
"""

import pytest
from aws.alb import ALBClient


class _FakeElbv2:
    def __init__(self, listeners):
        self._listeners = listeners
        self.calls = []

    def describe_listeners(self, **kwargs):
        self.calls.append(kwargs)
        return {"Listeners": self._listeners}


class _FakeSession:
    def __init__(self, listeners):
        self.elbv2 = _FakeElbv2(listeners)

    def client(self, name):
        assert name == "elbv2"
        return self.elbv2


def _client(listeners):
    return ALBClient(_FakeSession(listeners))


HTTP = {"ListenerArn": "arn:aws:elasticloadbalancing:::listener/http", "Port": 80}
HTTPS = {"ListenerArn": "arn:aws:elasticloadbalancing:::listener/https", "Port": 443}


def test_returns_the_only_listener_when_there_is_one():
    assert _client([HTTP]).get_listener_arn("alb") == HTTP["ListenerArn"]


@pytest.mark.parametrize("listeners", [[HTTP, HTTPS], [HTTPS, HTTP]])
def test_selects_port_80_regardless_of_response_order(listeners):
    """The regression. describe_listeners does not promise an order, so with a :443
    listener present the old [0] indexing was a coin flip."""
    assert _client(listeners).get_listener_arn("alb") == HTTP["ListenerArn"]


def test_returns_none_rather_than_falling_back_to_another_port():
    """No silent fallback: an ALB with only :443 has no path-rule target, and saying so
    is what lets the caller fail with a real message instead of misrouting."""
    assert _client([HTTPS]).get_listener_arn("alb") is None


def test_returns_none_when_the_alb_has_no_listeners():
    assert _client([]).get_listener_arn("alb") is None


def test_port_is_selectable_for_the_https_listener():
    assert _client([HTTP, HTTPS]).get_listener_arn("alb", port=443) == HTTPS["ListenerArn"]


def test_ignores_listeners_with_no_port_key():
    """A listener dict missing Port must not match by accident."""
    assert _client([{"ListenerArn": "arn:no-port"}]).get_listener_arn("alb") is None


def test_queries_the_requested_load_balancer():
    client = _client([HTTP])
    client.get_listener_arn("arn:aws:elasticloadbalancing:::loadbalancer/app/x/1")
    assert client.client.calls == [
        {"LoadBalancerArn": "arn:aws:elasticloadbalancing:::loadbalancer/app/x/1"}
    ]


def test_mock_session_listener_carries_a_port():
    """Mock-mode deploys go through the same selection path, so the stub has to model
    Port or every mock deploy strands on 'No :80 listener found'."""
    from api.mock.mock_session import MockSession

    session = MockSession(region="us-east-1", account_id="000000000000")
    listeners = session.client("elbv2").describe_listeners(LoadBalancerArn="alb")["Listeners"]
    assert [listener["Port"] for listener in listeners] == [80]
    assert ALBClient(session).get_listener_arn("alb") == listeners[0]["ListenerArn"]
