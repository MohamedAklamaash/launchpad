import json
import logging
import time
import uuid
import redis
import os

logger = logging.getLogger(__name__)

QUEUE_NAME = "notification-event"
JOB_NAME = "infra-notification-event"

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=os.environ.get("REDIS_PASSWORD", ""),
            db=int(os.environ.get("NOTIFICATION_REDIS_DB", 1)),
            decode_responses=True,
        )
    return _redis_client


def _resolve_recipient(user_id: str) -> tuple[str, str] | None:
    """Return (email, user_name) of the infra owner."""
    try:
        from api.models.user import User
        user = User.objects.get(id=user_id)
        return user.email, user.user_name
    except Exception as e:
        logger.warning(f"Could not resolve user {user_id}: {e}")
    return None


def _enqueue(user_id: str, email: str, user_name: str, infra_id: str, infra_name: str,
             event: str, error: str | None = None):
    """Push a BullMQ-compatible job onto the notification-event queue."""
    try:
        r = _get_redis()
        job_id = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)

        job_data = {
            "name": JOB_NAME,
            "data": json.dumps({
                "user_id": user_id,
                "email": email,
                "user_name": user_name,
                "infra_id": infra_id,
                "infra_name": infra_name,
                "event": event,
                "error": error,
            }),
            "opts": json.dumps({"attempts": 3, "backoff": {"type": "exponential", "delay": 1000}}),
            "timestamp": timestamp,
            "delay": 0,
            "priority": 0,
            "processedOn": 0,
            "finishedOn": 0,
            "returnvalue": "null",
            "stacktrace": "[]",
            "attemptsMade": 0,
        }

        pipe = r.pipeline()
        pipe.hset(f"bull:{QUEUE_NAME}:{job_id}", mapping=job_data)
        pipe.lpush(f"bull:{QUEUE_NAME}:wait", job_id)
        pipe.execute()

        logger.info(f"Enqueued infra notification job {job_id} for event={event} to {email}")
    except Exception as e:
        logger.error(f"Failed to enqueue notification for event={event}: {e}")


def _notify(event: str, user_id: str, infra_id: str, infra_name: str, error: str | None = None):
    """Resolve the owner and enqueue one event, or skip."""
    recipient = _resolve_recipient(user_id)
    if not recipient:
        logger.warning("No recipient found, skipping notification")
        return
    email, user_name = recipient
    _enqueue(user_id, email, user_name, infra_id, infra_name, event, error)


class NotificationService:
    """Send infra event notifications via the notification service queue."""

    @staticmethod
    def send_provision_success(user_id: str, infra_id: str, infra_name: str):
        _notify("provision_success", user_id, infra_id, infra_name)

    @staticmethod
    def send_provision_failure(user_id: str, infra_id: str, infra_name: str, error: str):
        _notify("provision_failure", user_id, infra_id, infra_name, error)

    @staticmethod
    def send_destroy_success(user_id: str, infra_id: str, infra_name: str):
        _notify("destroy_success", user_id, infra_id, infra_name)

    @staticmethod
    def send_destroy_failure(user_id: str, infra_id: str, infra_name: str, error: str):
        _notify("destroy_failure", user_id, infra_id, infra_name, error)

    @staticmethod
    def send_database_create_success(user_id: str, infra_id: str, infra_name: str):
        _notify("database_create_success", user_id, infra_id, infra_name)

    @staticmethod
    def send_database_create_failure(user_id: str, infra_id: str, infra_name: str, error: str):
        _notify("database_create_failure", user_id, infra_id, infra_name, error)

    @staticmethod
    def send_database_delete_success(user_id: str, infra_id: str, infra_name: str):
        _notify("database_delete_success", user_id, infra_id, infra_name)

    @staticmethod
    def send_database_delete_failure(user_id: str, infra_id: str, infra_name: str, error: str):
        _notify("database_delete_failure", user_id, infra_id, infra_name, error)
