"""The shared HTTP client's circuit breaker must count only an unhealthy remote (5xx,
network), never a caller's own 4xx. Real AWS (F7 ticket 9): one app's wrong branch made
GitHub answer 422 and opened the shared GitHub breaker for every app on the platform."""
from unittest.mock import MagicMock

import pytest
import requests
from shared.resilience.circuit_breaker import CircuitBreakerOpenError, CircuitState
from shared.resilience.http_client import ResilientHttpClient


def _response(status: int) -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res.url = "https://api.example.test/x"
    return res


def _client(status: int) -> ResilientHttpClient:
    client = ResilientHttpClient(name="test-breaker", base_url="https://api.example.test")
    client.session = MagicMock()
    client.session.request.return_value = _response(status)
    return client


@pytest.mark.parametrize("status", [400, 404, 409, 422, 429])
def test_client_errors_never_open_the_breaker(status):
    client = _client(status)
    for _ in range(client.breaker.failure_threshold * 3):
        assert client.get("/x").status_code == status
    assert client.breaker.state == CircuitState.CLOSED


def test_server_errors_still_open_the_breaker():
    client = _client(503)
    for _ in range(client.breaker.failure_threshold):
        with pytest.raises(requests.exceptions.HTTPError):
            client.get("/x")
    with pytest.raises(CircuitBreakerOpenError):
        client.get("/x")
