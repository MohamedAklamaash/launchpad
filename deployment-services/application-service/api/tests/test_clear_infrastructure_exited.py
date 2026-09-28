"""H2 (hardening) RECOMMENDED 2 — see plan/H-hardening.md: recovering from a bad/forged
infrastructure.exited event without raw SQL. The command must refuse unless
infrastructure-service's own row confirms the infrastructure is NOT actually exited."""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command

from api.services.exit_status_client import (
    ExitStatusLookupError,
    fetch_remote_exit_status,
)


@pytest.fixture
def exited_infra(schema_db):
    from django.utils import timezone

    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    return Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512, exited_at=timezone.now(),
    )


# ── the HTTP client ───────────────────────────────────────────────────────────

def test_fetch_remote_exit_status_returns_none_when_not_exited():
    response = MagicMock(status_code=200)
    response.json.return_value = {"infrastructure_id": "x", "exited_at": None}
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=response):
        assert fetch_remote_exit_status(str(uuid.uuid4())) is None


def test_fetch_remote_exit_status_returns_the_timestamp_when_exited():
    response = MagicMock(status_code=200)
    response.json.return_value = {"infrastructure_id": "x", "exited_at": "2026-01-01T00:00:00+00:00"}
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=response):
        assert fetch_remote_exit_status(str(uuid.uuid4())) == "2026-01-01T00:00:00+00:00"


def test_fetch_remote_exit_status_raises_on_non_200():
    response = MagicMock(status_code=404)
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=response), \
         pytest.raises(ExitStatusLookupError):
        fetch_remote_exit_status(str(uuid.uuid4()))


def test_fetch_remote_exit_status_raises_when_unreachable():
    with patch("shared.resilience.http_client.ResilientHttpClient.get", side_effect=ConnectionError("down")), \
         pytest.raises(ExitStatusLookupError):
        fetch_remote_exit_status(str(uuid.uuid4()))


# ── the management command ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_refuses_without_confirm(exited_infra):
    with pytest.raises(CommandError, match="--confirm"):
        call_command("clear_infrastructure_exited", infrastructure_id=str(exited_infra.id))

    exited_infra.refresh_from_db()
    assert exited_infra.exited_at is not None


@pytest.mark.django_db
def test_refuses_when_infrastructure_service_confirms_it_is_exited(exited_infra):
    with patch(
        "api.services.exit_status_client.fetch_remote_exit_status",
        return_value="2026-01-01T00:00:00+00:00",
    ), pytest.raises(CommandError, match="genuinely exited"):
        call_command("clear_infrastructure_exited", infrastructure_id=str(exited_infra.id), confirm=True)

    exited_infra.refresh_from_db()
    assert exited_infra.exited_at is not None


@pytest.mark.django_db
def test_refuses_when_infrastructure_service_is_unreachable(exited_infra):
    with patch(
        "api.services.exit_status_client.fetch_remote_exit_status",
        side_effect=ExitStatusLookupError("down"),
    ), pytest.raises(CommandError, match="could not confirm"):
        call_command("clear_infrastructure_exited", infrastructure_id=str(exited_infra.id), confirm=True)

    exited_infra.refresh_from_db()
    assert exited_infra.exited_at is not None


@pytest.mark.django_db
def test_clears_when_infrastructure_service_confirms_not_exited(exited_infra):
    with patch("api.services.exit_status_client.fetch_remote_exit_status", return_value=None):
        call_command("clear_infrastructure_exited", infrastructure_id=str(exited_infra.id), confirm=True)

    exited_infra.refresh_from_db()
    assert exited_infra.exited_at is None


@pytest.mark.django_db
def test_is_a_noop_when_not_exited_locally(schema_db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=1024, max_memory=512,
    )

    with patch("api.services.exit_status_client.fetch_remote_exit_status") as mock_fetch:
        call_command("clear_infrastructure_exited", infrastructure_id=str(infra.id), confirm=True)

    mock_fetch.assert_not_called()


@pytest.mark.django_db
def test_refuses_for_an_unknown_local_infrastructure():
    with pytest.raises(CommandError, match="No local Infrastructure row"):
        call_command("clear_infrastructure_exited", infrastructure_id=str(uuid.uuid4()), confirm=True)


@pytest.mark.django_db
def test_logs_the_operator_and_previous_value_on_clear(exited_infra, caplog):
    import logging

    previous = exited_infra.exited_at
    with patch("api.services.exit_status_client.fetch_remote_exit_status", return_value=None), \
         caplog.at_level(logging.WARNING, logger="api.management.commands.clear_infrastructure_exited"):
        call_command("clear_infrastructure_exited", infrastructure_id=str(exited_infra.id), confirm=True)

    logged = "\n".join(r.message for r in caplog.records)
    assert str(exited_infra.id) in logged
    assert str(previous) in logged
