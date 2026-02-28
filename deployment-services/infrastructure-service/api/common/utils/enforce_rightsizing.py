import logging
import boto3
from django.core.management.base import BaseCommand
from api.repositories.infrastructure import InfrastructureRepository
from api.cloud_providers.aws.authenticate import authenticate_infrastructure

def enforce_rightsizing():
    logger = logging.getLogger(__name__)
    logger.info("Starting AWS Compute Optimizer enforcement job...")
    repo = InfrastructureRepository()
    
    infras = repo.get_all()
    processed_count = 0
    
    for infra in infras:
        if infra.cloud_provider != "AWS":
            continue

        try:
            authenticate_infrastructure(infra)
            metadata = infra.metadata or {}
            
            compute_optimizer = boto3.client(
                "compute-optimizer",
                region_name=metadata.get("aws_region", "us-west-2"),
                aws_access_key_id=metadata.get("aws_access_key_id", ""),
                aws_secret_access_key=metadata.get("aws_secret_access_key", ""),
                aws_session_token=metadata.get("aws_session_token", ""),
            )
            
            ec2 = boto3.client(
                "ec2",
                region_name=metadata.get("aws_region", "us-west-2"),
                aws_access_key_id=metadata.get("aws_access_key_id", ""),
                aws_secret_access_key=metadata.get("aws_secret_access_key", ""),
                aws_session_token=metadata.get("aws_session_token", ""),
            )
            
            logger.info(f"Querying EC2 recommendations for infra ID: {infra.id}")
            
            response = compute_optimizer.get_ec2_instance_recommendations()
            recommendations = response.get("instanceRecommendations", [])
            
            for rec in recommendations:
                instance_id = rec.get("instanceArn").split("/")[-1]
                finding = rec.get("finding")
                
                if finding == "OVER_PROVISIONED" or finding == "UNDER_PROVISIONED":
                    options = rec.get("recommendationOptions", [])
                    if options:
                        best_option = options[0]
                        target_type = best_option.get("instanceType")
                        
                        logger.warning(f"Rightsizing Instance {instance_id} -> {target_type} ({finding})")
                        
                        try:
                            # Step A: Stop instance safely
                            ec2.stop_instances(InstanceIds=[instance_id])
                            waiter = ec2.get_waiter('instance_stopped')
                            waiter.wait(InstanceIds=[instance_id])
                            
                            # Step B: Modify instance type attribute
                            ec2.modify_instance_attribute(
                                InstanceId=instance_id,
                                InstanceType={'Value': target_type}
                            )
                            
                            # Step C: Start instance
                            ec2.start_instances(InstanceIds=[instance_id])
                            logger.info(f"Successfully modified {instance_id} to {target_type}")
                        except Exception as apply_err:
                            logger.error(f"Failed to right-size {instance_id}: {apply_err}")
                    
            processed_count += 1
            
        except Exception as e:
            logger.error(f"Error querying Optimizer for Infra {infra.id}: {e}", exc_info=True)

    logger.info(f"Finished. Enforced recommendations for {processed_count} infrastructures.")