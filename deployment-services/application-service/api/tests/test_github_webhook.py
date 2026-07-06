import hashlib
import hmac
import json
import uuid
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from rest_framework.test import APIRequestFactory


@pytest.fixture
def app_row(schema_db):
    from api.models.user import User
    from api.models.infrastructure import Infrastructure
    from api.models.application import Application

    u = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u")
    infra = Infrastructure.objects.create(
        user=u, name="i", cloud_provider="aws", max_cpu=1, max_memory=1, code="123456789012"
    )
    secret = "whsec-" + uuid.uuid4().hex
    app = Application.objects.create(
        name="a", user=u, infrastructure=infra, project_remote_url="r",
        project_branch="main", project_commit_hash="c", github_webhook_secret=secret,
    )
    return app, secret


def _post(app_id, raw_body, content_type, secret, event="push", good_sig=True):
    from api.views.application import application_github_webhook

    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not good_sig:
        sig = "sha256=" + "0" * 64
    req = APIRequestFactory().post(
        f"/api/v1/webhooks/github/{app_id}/", data=body, content_type=content_type,
        HTTP_X_GITHUB_EVENT=event, HTTP_X_HUB_SIGNATURE_256=sig,
    )
    return application_github_webhook(req, app_id=str(app_id))


@pytest.mark.django_db
@patch("api.views.application.DeploymentQueue.enqueue_deployment")
def test_json_push_deploys(mock_enqueue, app_row):
    app, secret = app_row
    resp = _post(app.id, json.dumps({"ref": "refs/heads/main"}), "application/json", secret)
    assert resp.status_code == 202
    mock_enqueue.assert_called_once()


@pytest.mark.django_db
@patch("api.views.application.DeploymentQueue.enqueue_deployment")
def test_form_urlencoded_push_deploys(mock_enqueue, app_row):
    app, secret = app_row
    body = urlencode({"payload": json.dumps({"ref": "refs/heads/main"})})
    resp = _post(app.id, body, "application/x-www-form-urlencoded", secret)
    assert resp.status_code == 202
    mock_enqueue.assert_called_once()


@pytest.mark.django_db
@patch("api.views.application.DeploymentQueue.enqueue_deployment")
def test_forged_signature_is_rejected(mock_enqueue, app_row):
    app, secret = app_row
    resp = _post(app.id, json.dumps({"ref": "refs/heads/main"}), "application/json", secret, good_sig=False)
    assert resp.status_code == 401
    mock_enqueue.assert_not_called()


@pytest.mark.django_db
@patch("api.views.application.DeploymentQueue.enqueue_deployment")
def test_push_to_untracked_branch_ignored(mock_enqueue, app_row):
    app, secret = app_row
    resp = _post(app.id, json.dumps({"ref": "refs/heads/feature"}), "application/json", secret)
    assert resp.status_code == 200
    assert resp.data["status"] == "ignored"
    mock_enqueue.assert_not_called()


@pytest.mark.django_db
@patch("api.views.application.DeploymentQueue.enqueue_deployment")
def test_empty_tracked_branch_ignores_instead_of_deploying_every_branch(mock_enqueue, app_row):
    app, secret = app_row
    app.project_branch = ""
    app.save(update_fields=["project_branch"])
    resp = _post(app.id, json.dumps({"ref": "refs/heads/whatever"}), "application/json", secret)
    assert resp.status_code == 200
    assert "no tracked branch" in resp.data["reason"]
    mock_enqueue.assert_not_called()
