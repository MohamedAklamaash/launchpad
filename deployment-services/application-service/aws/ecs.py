import boto3
import logging

logger = logging.getLogger(__name__)

class ECSClient:
    def __init__(self, session):
        self.client = session.client('ecs')
    
    def create_task_definition(self, family, image, cpu, memory, envs, execution_role_arn, container_port=8000):
        env_vars = [{'name': k, 'value': str(v)} for k, v in (envs or {}).items()]
        
        # Convert CPU and memory to valid Fargate values
        # CPU is in vCPU (e.g., 0.25, 0.5, 1, 2, 4)
        # Memory is in GB (e.g., 0.5, 1, 2, 4, 8)
        
        # Valid Fargate CPU/Memory combinations:
        # 0.25 vCPU: 0.5GB, 1GB, 2GB
        # 0.5 vCPU: 1GB, 2GB, 3GB, 4GB
        # 1 vCPU: 2GB, 3GB, 4GB, 5GB, 6GB, 7GB, 8GB
        # 2 vCPU: 4GB-16GB
        # 4 vCPU: 8GB-30GB
        
        if cpu <= 0.25:
            cpu_str = "256"
            memory = max(0.5, memory)
            memory = min(2, memory)
        elif cpu <= 0.5:
            cpu_str = "512"
            memory = max(1, memory)
            memory = min(4, memory)
        elif cpu <= 1:
            cpu_str = "1024"
            memory = max(2, memory)
            memory = min(8, memory)
        elif cpu <= 2:
            cpu_str = "2048"
            memory = max(4, memory)
            memory = min(16, memory)
        else:
            cpu_str = "4096"
            memory = max(8, memory)
            memory = min(30, memory)
        
        memory_str = str(int(memory * 1024))
        
        response = self.client.register_task_definition(
            family=family,
            networkMode='awsvpc',
            requiresCompatibilities=['FARGATE'],
            cpu=cpu_str,
            memory=memory_str,
            executionRoleArn=execution_role_arn,
            containerDefinitions=[{
                'name': family,
                'image': image,
                'essential': True,
                'environment': env_vars,
                'portMappings': [{
                    'containerPort': container_port,
                    'protocol': 'tcp'
                }],
                'logConfiguration': {
                    'logDriver': 'awslogs',
                    'options': {
                        'awslogs-group': f'/ecs/{family}',
                        'awslogs-region': self.client.meta.region_name,
                        'awslogs-stream-prefix': 'ecs'
                    }
                }
            }]
        )
        return response['taskDefinition']['taskDefinitionArn']
    
    def create_service(self, cluster_arn, service_name, task_definition_arn, target_group_arn, subnet_ids, security_group_ids, container_name, container_port=8000):
        try:
            response = self.client.create_service(
                cluster=cluster_arn,
                serviceName=service_name,
                taskDefinition=task_definition_arn,
                desiredCount=1,
                launchType='FARGATE',
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': subnet_ids,
                        'securityGroups': security_group_ids,
                        'assignPublicIp': 'DISABLED'
                    }
                },
                loadBalancers=[{
                    'targetGroupArn': target_group_arn,
                    'containerName': container_name,
                    'containerPort': container_port
                }]
            )
            return response['service']['serviceArn']
        except Exception as e:
            if 'already exists' in str(e).lower():
                logger.warning(f"Service {service_name} already exists, fetching ARN")
                response = self.client.describe_services(
                    cluster=cluster_arn,
                    services=[service_name]
                )
                if response['services']:
                    return response['services'][0]['serviceArn']
            raise
    
    def wait_for_service_stable(self, cluster_arn, service_name, timeout=300):
        """Wait for ECS service to become stable"""
        import time
        logger.info(f"Waiting for service {service_name} to become stable...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            response = self.client.describe_services(
                cluster=cluster_arn,
                services=[service_name]
            )
            
            if not response['services']:
                raise Exception(f"Service {service_name} not found")
            
            service = response['services'][0]
            
            # Check if service is stable (running tasks == desired tasks)
            running_count = service.get('runningCount', 0)
            desired_count = service.get('desiredCount', 0)
            
            if running_count == desired_count and running_count > 0:
                logger.info(f"Service {service_name} is stable with {running_count} running tasks")
                return True
            
            logger.info(f"Service {service_name}: {running_count}/{desired_count} tasks running, waiting...")
            time.sleep(10)
        
        raise Exception(f"Service {service_name} did not become stable within {timeout} seconds")
