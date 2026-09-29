"""First provision after onboarding races IAM propagation of LaunchpadDeploymentPolicy
(found on real AWS): the state-bucket call is denied for ~10-60s, so it is retried."""
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from api.services.terraform_worker import TerraformWorker


def _denied():
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, "CreateBucket")


def test_access_denied_is_retried_until_iam_propagates():
    calls = iter([_denied(), _denied(), ("bucket", "table")])

    def fake(*_a, **_k):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    with patch.object(TerraformWorker, "_ensure_backend", side_effect=fake), \
         patch("api.services.terraform_worker.time.sleep") as sleep:
        assert TerraformWorker._ensure_backend_with_iam_retry({}, "us-east-1", "1") == ("bucket", "table")
    assert sleep.call_count == 2


def test_gives_up_after_the_retry_budget():
    with patch.object(TerraformWorker, "_ensure_backend", side_effect=_denied()), \
         patch("api.services.terraform_worker.time.sleep"), pytest.raises(ClientError):
        TerraformWorker._ensure_backend_with_iam_retry({}, "us-east-1", "1")


def test_other_errors_are_not_retried():
    other = ClientError({"Error": {"Code": "InvalidBucketName", "Message": "x"}}, "CreateBucket")
    with patch.object(TerraformWorker, "_ensure_backend", side_effect=other) as ensure, \
         patch("api.services.terraform_worker.time.sleep") as sleep, pytest.raises(ClientError):
        TerraformWorker._ensure_backend_with_iam_retry({}, "us-east-1", "1")
    assert ensure.call_count == 1 and sleep.call_count == 0
