import json

from api.services import deployment_lock
from api.services import deployment_queue as dq


class _FakeRedis:
    def __init__(self, processing):
        self.processing = list(processing)
        self.main = []

    def _bucket(self, key):
        return self.processing if "processing" in key else self.main

    def lrange(self, key, start, end):
        return list(self._bucket(key))

    def rpush(self, key, value):
        self._bucket(key).append(value)

    def lrem(self, key, count, value):
        bucket = self._bucket(key)
        if value in bucket:
            bucket.remove(value)
            return 1
        return 0


def test_reaper_requeues_orphans_but_skips_locked_and_claimed(monkeypatch):
    # Regression (#5): a job actively deploying (locked) or still waiting in a live worker's
    # in-memory infra queue (claimed) must NOT be reaped — only genuinely orphaned jobs are.
    proc = [
        json.dumps({"app_id": "locked"}),
        json.dumps({"app_id": "claimed"}),
        json.dumps({"app_id": "orphan"}),
    ]
    fake = _FakeRedis(proc)
    monkeypatch.setattr(dq.DeploymentQueue, "get_redis", staticmethod(lambda: fake))
    monkeypatch.setattr(deployment_lock.DeploymentLock, "__init__", lambda self: None)
    monkeypatch.setattr(deployment_lock.DeploymentLock, "is_locked", lambda self, app: app == "locked")

    reaped = dq.DeploymentQueue.reap_orphaned_processing_jobs(is_claimed=lambda a: a == "claimed")

    assert reaped == 1
    assert any("orphan" in m for m in fake.main)
    assert not any("locked" in m for m in fake.main)
    assert not any("claimed" in m for m in fake.main)
    assert not any("orphan" in m for m in fake.processing)
    assert any("locked" in m for m in fake.processing)
