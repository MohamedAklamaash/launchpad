"""Security review re-verification item 2: run_worker's custom-domain tick must never
block dispatch waiting on a previous run (no future.result()/exception(timeout=...)).
It checks only .done() and skips resubmission outright when a previous run is still
in flight.

dispatch_custom_domain_check() is the extracted, side-effect-injected decision+submit
function the dispatch loop calls each tick — tested directly here since the loop
itself is a nested closure inside Command.handle().
"""
from concurrent.futures import Future, ThreadPoolExecutor

from api.management.commands.run_worker import dispatch_custom_domain_check
from django.test import TestCase


class _FakeRedis:
    def __init__(self, allow_set=True):
        self.allow_set = allow_set
        self.set_calls = []

    def set(self, key, value, nx=True, ex=None):
        self.set_calls.append((key, value, nx, ex))
        return self.allow_set


class DispatchCustomDomainCheckTests(TestCase):
    def setUp(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.pool.shutdown, wait=True)

    def test_skips_submission_and_never_blocks_when_previous_future_not_done(self):
        block = Future()
        redis_client = _FakeRedis(allow_set=True)
        task_calls = []

        future, started_at = dispatch_custom_domain_check(
            redis_client=redis_client,
            lock_key="lock",
            interval_seconds=300,
            pool=self.pool,
            task_fn=lambda: task_calls.append(1),
            worker_id="w1",
            previous_future=block,
            previous_started_at=123.0,
            now_fn=lambda: 200.0,
        )

        self.assertIs(future, block)
        self.assertEqual(started_at, 123.0)
        self.assertEqual(redis_client.set_calls, [])
        self.assertEqual(task_calls, [])
        block.set_result(None)

    def test_submits_when_no_previous_future(self):
        redis_client = _FakeRedis(allow_set=True)
        task_calls = []

        future, started_at = dispatch_custom_domain_check(
            redis_client=redis_client,
            lock_key="lock",
            interval_seconds=300,
            pool=self.pool,
            task_fn=lambda: task_calls.append(1),
            worker_id="w1",
            previous_future=None,
            previous_started_at=None,
            now_fn=lambda: 200.0,
        )

        future.result(timeout=5)
        self.assertEqual(started_at, 200.0)
        self.assertEqual(task_calls, [1])
        self.assertEqual(len(redis_client.set_calls), 1)

    def test_submits_when_previous_future_is_done(self):
        redis_client = _FakeRedis(allow_set=True)
        done_future = Future()
        done_future.set_result(None)
        task_calls = []

        future, started_at = dispatch_custom_domain_check(
            redis_client=redis_client,
            lock_key="lock",
            interval_seconds=300,
            pool=self.pool,
            task_fn=lambda: task_calls.append(1),
            worker_id="w1",
            previous_future=done_future,
            previous_started_at=100.0,
            now_fn=lambda: 200.0,
        )

        future.result(timeout=5)
        self.assertIsNot(future, done_future)
        self.assertEqual(started_at, 200.0)
        self.assertEqual(task_calls, [1])

    def test_does_not_submit_when_fleet_wide_lock_not_acquired(self):
        redis_client = _FakeRedis(allow_set=False)
        task_calls = []

        future, started_at = dispatch_custom_domain_check(
            redis_client=redis_client,
            lock_key="lock",
            interval_seconds=300,
            pool=self.pool,
            task_fn=lambda: task_calls.append(1),
            worker_id="w1",
            previous_future=None,
            previous_started_at=None,
            now_fn=lambda: 200.0,
        )

        self.assertIsNone(future)
        self.assertIsNone(started_at)
        self.assertEqual(task_calls, [])

    def test_does_not_call_result_or_exception_on_a_pending_previous_future(self):
        class _NoBlockFuture(Future):
            def result(self, timeout=None):
                raise AssertionError("dispatch must never call .result() on the previous future")

            def exception(self, timeout=None):
                raise AssertionError("dispatch must never call .exception() on the previous future")

        pending = _NoBlockFuture()
        redis_client = _FakeRedis(allow_set=True)

        future, _started_at = dispatch_custom_domain_check(
            redis_client=redis_client,
            lock_key="lock",
            interval_seconds=300,
            pool=self.pool,
            task_fn=lambda: None,
            worker_id="w1",
            previous_future=pending,
            previous_started_at=50.0,
            now_fn=lambda: 200.0,
        )

        self.assertIs(future, pending)
        pending.set_result(None)
