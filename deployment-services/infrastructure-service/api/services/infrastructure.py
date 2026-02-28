import os
import uuid
import logging
import threading
from django.db import transaction
from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
from shared.resilience.circuit_breaker import CircuitBreaker
from api.messaging.producer.producer import infra_producer
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from shared.enums.cloud_provider import CloudProvider
from api.services.terraform import TerraformService

logger = logging.getLogger(__name__)

cloud_cb = CircuitBreaker(
    name="CloudProviderAPI",
    failure_threshold=int(os.environ.get("CB_FAILURE_THRESHOLD", 5)),
    timeout=float(os.environ.get("CB_TIMEOUT_MS", 30000)) / 1000.0,
    success_threshold=int(os.environ.get("CB_SUCCESS_THRESHOLD", 2))
)


class InfrastructureService:
    def __init__(self):
        self.repo = InfrastructureRepository()

    def get_all_for_user(self, user_id):
        infras = self.repo.get_all_for_user(user_id)
        return InfrastructureSerializer.serialize_list(infras)

    def get_infrastructure(self, user_id, infra_id):
        infra = self.repo.get_by_id(user_id, infra_id)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None

    def create_infrastructure(self, user_id, infra_data):
        """
        Create an Infrastructure row and publish the InfrastructureCreated event,
        validating authentication synchronously first.
        """
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            infra = self.repo.create(user_id, infra_data)
            
            if infra.cloud_provider == CloudProvider.AWS:
                authenticate_infrastructure(infra)
                infra.refresh_from_db()

            serialized_infra = InfrastructureSerializer.serialize_instance(infra)

            infra_id = serialized_infra["id"]
            cloud_provider = serialized_infra.get("cloud_provider")
            max_cpu = serialized_infra.get("max_cpu", 0)
            max_memory = serialized_infra.get("max_memory", 0)
            invited_users = serialized_infra.get("invited_users", [])
            metadata = serialized_infra.get("metadata") or {}
            code = serialized_infra.get("code", "")

        def _on_commit_actions():
            # 1. Provision Infrastructure Asynchronously (AWS specific)
            if cloud_provider == CloudProvider.AWS:
                def _provision_aws():
                    try:
                        infra_obj = self.repo.get_by_id(user_id, infra_id)
                        if not infra_obj:
                            return
                        logger.info(f"Authenticating infrastructure {infra_id}...")
                        authenticate_infrastructure(infra_obj)
                        
                        infra_obj.refresh_from_db()
                        current_metadata = infra_obj.metadata or {}
                        
                        tf_config = {
                            "environment": f"cli-{infra_id}", # cli meaning client to overcome the 64 chr limit
                            "owner": str(user_id),
                            "project": "launchpad-infra",
                        }
                        
                        logger.info(f"Triggering asynchronous Terraform apply for infrastructure {infra_id}...")
                        tf_result = TerraformService.apply(credentials=current_metadata, config=tf_config)
                        
                        if not tf_result["success"]:
                            logger.error(f"Terraform apply failed for infra {infra_id}: {tf_result.get('error')}")
                        else:
                            logger.info(f"Terraform apply SUCCEEDED for infra {infra_id}")
                            try:
                                final_infra = self.repo.get_by_id(user_id, infra_id)
                                final_metadata = final_infra.metadata or {}
                                
                                infra_producer.publish_infra_created(
                                    user_id=user_id,
                                    infra_id=infra_id,
                                    name=final_infra.name,
                                    cloud_provider=cloud_provider,
                                    max_cpu=final_infra.max_cpu,
                                    max_memory=final_infra.max_memory,
                                    invited_users=[str(uid) for uid in invited_users],
                                    metadata=final_metadata,
                                    correlation_id=correlation_id,
                                )
                                logger.info(f"Published infra_created event for {infra_id} post-provisioning.")
                            except Exception as e:
                                logger.error(f"Failed to publish infra_created event after successful provisioning: {str(e)}")
                            
                    except Exception as e:
                        error_msg = str(e)
                        logger.error(f"Failed to authenticate/provision AWS infrastructure {infra_id}: {error_msg}")
                        try:
                            fail_infra = self.repo.get_by_id(user_id, infra_id)
                            if fail_infra:
                                meta = fail_infra.metadata or {}
                                fail_infra.metadata = {**meta, "provisioning_error": error_msg}
                                fail_infra.is_cloud_authenticated = False
                                fail_infra.save()
                        except Exception as inner_e:
                            logger.error(f"Failed to save error status to infra record: {str(inner_e)}")

                threading.Thread(target=_provision_aws, daemon=True).start()
            
        transaction.on_commit(_on_commit_actions)

        logger.info(
            f"Infrastructure DB record created for {infra_id}. Starting asynchronous AWS provisioning...",
            extra={
                "correlation_id": correlation_id,
                "infra_id": infra_id,
                "user_id": str(user_id),
            },
        )
        return serialized_infra

    def delete_infrastructure(self, user_id, infra_id):
        infra = self.repo.get_by_id(user_id, infra_id)
        if infra and infra.cloud_provider == CloudProvider.AWS:
            try:
                logger.info(f"Authenticating infrastructure {infra_id}...")
                authenticate_infrastructure(infra)
                
                metadata = infra.metadata or {}
                
                tf_config = {
                    "environment": f"cli-{infra_id}",
                    "owner": str(user_id),
                    "project": "launchpad-infra",
                }
                tf_result = TerraformService.destroy(credentials=metadata, config=tf_config)
                if not tf_result["success"]:
                    logger.error(f"Terraform destroy failed for infra {infra_id}: {tf_result.get('error')}")
            except Exception as e:
                logger.error(f"Error triggering Terraform destroy for {infra_id}: {str(e)}")

        return self.repo.delete(user_id, infra_id)

    def update_infrastructure(self, user_id, infra_id, update_data):
        infra = self.repo.update(user_id, infra_id, update_data)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None