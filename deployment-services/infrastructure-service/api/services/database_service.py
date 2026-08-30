import logging

from api.cloud_providers.aws.iam_precheck import (
    PolicyRefreshRequired,
    precheck_database_create,
)
from api.models.database import Database
from api.models.environment import Environment
from api.repositories.infrastructure import InfrastructureRepository
from api.services.infra_queue import InfraQueue
from api.validators import validate_database_name
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


class PolicyRefreshRequiredError(ValueError):
    """Raised when the customer's assumed role can't perform the create actions yet.

    Distinguished from a generic ValueError so the view can return a 422 with a
    machine-readable code instead of the usual 400.
    """

    def __init__(self, message, denied_actions=None):
        super().__init__(message)
        self.denied_actions = denied_actions or []


class DatabaseService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()

    def _get_environment_or_raise(self, user_id, infra_id):
        # Owner-OR-invited resolution closes the cross-tenant IDOR path: an infra_id must
        # resolve through the same predicate infrastructure-service uses everywhere else,
        # never a bare Environment/Database lookup by id. Invited members may still view
        # (list/get) — write operations enforce owner-only themselves, below.
        infra = self.infra_repo.get_by_id(user_id, infra_id)
        if not infra:
            raise LookupError("Infrastructure not found")
        try:
            env = Environment.objects.get(infrastructure_id=infra.id)
        except Environment.DoesNotExist:
            raise LookupError("Environment not found")
        return env, infra

    def _require_owner(self, user_id, infra):
        if str(infra.user_id) != str(user_id):
            raise PermissionError("Only the infrastructure owner can manage its databases")

    def list_databases(self, user_id, infra_id):
        env, _ = self._get_environment_or_raise(user_id, infra_id)
        return Database.objects.filter(environment=env).exclude(status='DELETED').order_by('-created_at')

    def get_database(self, user_id, infra_id, database_id):
        env, _ = self._get_environment_or_raise(user_id, infra_id)
        try:
            return Database.objects.get(id=database_id, environment=env)
        except Database.DoesNotExist:
            raise LookupError("Database not found")

    def create_database(self, user_id, infra_id, data: dict) -> Database:
        env, infra = self._get_environment_or_raise(user_id, infra_id)
        self._require_owner(user_id, infra)

        if env.status != 'ACTIVE':
            raise ValueError(f"Environment must be ACTIVE to create a database (currently {env.status})")

        name = str(data.get('name', '')).strip()
        validate_database_name(name)

        engine = data.get('engine')
        if engine not in settings.DATABASE_ENGINE_VERSIONS:
            raise ValueError(f"Unsupported engine: {engine}")

        engine_version = data.get('engine_version')
        if engine_version not in settings.DATABASE_ENGINE_VERSIONS[engine]:
            raise ValueError(f"Unsupported engine_version for {engine}: {engine_version}")

        instance_class = data.get('instance_class')
        if instance_class not in settings.DATABASE_INSTANCE_CLASSES[engine]:
            raise ValueError(f"Unsupported instance_class for {engine}: {instance_class}")

        allocated_storage = None
        if engine != 'redis':
            try:
                allocated_storage = int(data.get('allocated_storage'))
            except (TypeError, ValueError):
                raise ValueError("allocated_storage is required and must be an integer (GB)")
            if not (settings.DATABASE_MIN_STORAGE_GB <= allocated_storage <= settings.DATABASE_MAX_STORAGE_GB):
                raise ValueError(
                    f"allocated_storage must be between {settings.DATABASE_MIN_STORAGE_GB} and "
                    f"{settings.DATABASE_MAX_STORAGE_GB} GB"
                )

        if Database.objects.filter(environment=env, name=name).exclude(status='DELETED').exists():
            raise ValueError(f"A database named '{name}' already exists in this environment")

        live_count = Database.objects.filter(environment=env).exclude(status='DELETED').count()
        if live_count >= settings.MAX_DATABASES_PER_INFRA:
            raise ValueError(f"Database quota reached ({settings.MAX_DATABASES_PER_INFRA} per infrastructure)")

        try:
            precheck_database_create(infra, engine)
        except PolicyRefreshRequired as e:
            raise PolicyRefreshRequiredError(
                "Launchpad's IAM role in your AWS account is missing permissions for this "
                "operation. Re-run the refresh policy script and try again.",
                denied_actions=e.denied_actions,
            )

        with transaction.atomic():
            db = Database.objects.create(
                environment=env, name=name, engine=engine, engine_version=engine_version,
                instance_class=instance_class, allocated_storage=allocated_storage, status='PENDING',
            )
            transaction.on_commit(lambda: InfraQueue.enqueue_provision(str(infra_id)))

        logger.info(f"Database {db.id} ({name}/{engine}) queued for infra {infra_id}")
        return db

    def delete_database(self, user_id, infra_id, database_id, confirm_name: str):
        env, infra = self._get_environment_or_raise(user_id, infra_id)
        self._require_owner(user_id, infra)
        try:
            db = Database.objects.get(id=database_id, environment=env)
        except Database.DoesNotExist:
            raise LookupError("Database not found")

        if db.status == 'DELETED':
            raise ValueError("Database is already deleted")
        if db.status == 'DELETING':
            raise ValueError("Database delete is already in progress")
        if confirm_name != db.name:
            raise ValueError("confirm_name does not match the database name")

        with transaction.atomic():
            db.status = 'DELETING'
            db.save(update_fields=['status', 'updated_at'])
            transaction.on_commit(lambda: InfraQueue.enqueue_provision(str(infra_id)))

        logger.info(f"Database {db.id} queued for deletion on infra {infra_id}")
        return db
