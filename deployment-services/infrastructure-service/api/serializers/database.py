from typing import Any

from api.models.database import Database


class DatabaseSerializer:
    """Serializes Database rows. Never emits a credential — host/port/secret ARN only;
    the secret's value lives exclusively in AWS Secrets Manager."""

    @staticmethod
    def serialize_instance(instance: Database) -> dict[str, Any]:
        return {
            'id': str(instance.id),
            'environment_id': str(instance.environment_id),
            'name': instance.name,
            'engine': instance.engine,
            'engine_version': instance.engine_version,
            'instance_class': instance.instance_class,
            'allocated_storage': instance.allocated_storage,
            'status': instance.status,
            'host': instance.host,
            'port': instance.port,
            'secret_arn': instance.secret_arn,
            'error_message': instance.error_message,
            'created_at': instance.created_at,
            'updated_at': instance.updated_at,
        }

    @staticmethod
    def serialize_list(instances: list[Database]) -> list[dict[str, Any]]:
        return [DatabaseSerializer.serialize_instance(i) for i in instances]
