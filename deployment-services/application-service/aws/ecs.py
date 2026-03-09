import boto3
import logging

logger = logging.getLogger(__name__)

class ECSClient:
    def __init__(self, session):
        self.client = session.client('ecs')
    
    def create_task_definition(self, family, image, cpu, memory, envs, execution_role_arn, container_port=8000, app_name=None):
        env_vars = [{'name': k, 'value': str(v)} for k, v in (envs or {}).items()]
        logger.info(f"Creating task definition with {len(env_vars)} environment variables: {list(envs.keys()) if envs else []}")
        
        # Convert CPU and memory to valid Fargate values
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
        
        # Create NGINX config for path stripping
        nginx_config = self._generate_nginx_config(app_name, container_port) if app_name else None
        
        container_definitions = []
        
        # Add application container FIRST
        container_definitions.append({
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
                    'awslogs-stream-prefix': 'app'
                }
            }
        })
        
        # Add NGINX sidecar if app_name provided (for path stripping)
        if nginx_config:
            # Base64 encode to avoid shell escaping issues
            import base64
            nginx_config_b64 = base64.b64encode(nginx_config.encode()).decode()
            
            container_definitions.append({
                'name': f'{family}-nginx',
                'image': 'public.ecr.aws/nginx/nginx:alpine',
                'essential': True,
                'dependsOn': [{
                    'containerName': family,
                    'condition': 'START'
                }],
                'portMappings': [{
                    'containerPort': 80,
                    'protocol': 'tcp'
                }],
                'environment': [
                    {'name': 'NGINX_CONFIG_B64', 'value': nginx_config_b64}
                ],
                'command': [
                    '/bin/sh', '-c',
                    'echo "$NGINX_CONFIG_B64" | base64 -d > /etc/nginx/nginx.conf && '
                    'echo "Waiting for app to be ready..." && '
                    'for i in $(seq 1 30); do '
                    '  if nc -z localhost 8000 2>/dev/null; then '
                    '    echo "App port is open!"; '
                    '    sleep 2; '
                    '    if wget -q -O- --timeout=5 http://localhost:8000/ > /dev/null 2>&1; then '
                    '      echo "App is responding! Starting NGINX..."; '
                    '      break; '
                    '    fi; '
                    '  fi; '
                    '  echo "Waiting... ($i/30)"; '
                    '  sleep 2; '
                    'done && '
                    'nginx -g "daemon off;"'
                ],
                'logConfiguration': {
                    'logDriver': 'awslogs',
                    'options': {
                        'awslogs-group': f'/ecs/{family}',
                        'awslogs-region': self.client.meta.region_name,
                        'awslogs-stream-prefix': 'nginx'
                    }
                }
            })
        
        response = self.client.register_task_definition(
            family=family,
            networkMode='awsvpc',
            requiresCompatibilities=['FARGATE'],
            cpu=cpu_str,
            memory=memory_str,
            executionRoleArn=execution_role_arn,
            containerDefinitions=container_definitions
        )
        return response['taskDefinition']['taskDefinitionArn']
    
    def _generate_nginx_config(self, app_name, backend_port):
        """Generate NGINX config with proper path rewriting for multi-app ALB"""
        return f'''
events {{
    worker_connections 1024;
}}
http {{
    proxy_connect_timeout 60s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
    
    access_log /dev/stdout;
    error_log /dev/stderr info;
    
    upstream backend {{
        server localhost:{backend_port} max_fails=0 fail_timeout=0;
    }}
    
    server {{
        listen 80;
        
        # ALB health check - responds to root path only
        location = / {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}
        
        # App routes - strip prefix and proxy everything
        location /{app_name} {{
            rewrite ^/{app_name}/?(.*)$ /$1 break;
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }}
    }}
}}
'''
    
    def create_service(self, cluster_arn, service_name, task_definition_arn, target_group_arn, subnet_ids, security_group_ids, container_name, container_port=8000):
        try:
            # Check if service already exists
            try:
                response = self.client.describe_services(
                    cluster=cluster_arn,
                    services=[service_name]
                )
                if response['services'] and response['services'][0]['status'] != 'INACTIVE':
                    existing_service = response['services'][0]
                    logger.info(f"Service {service_name} already exists, updating it")
                    
                    # Update existing service
                    self.client.update_service(
                        cluster=cluster_arn,
                        service=service_name,
                        taskDefinition=task_definition_arn,
                        desiredCount=1
                    )
                    return existing_service['serviceArn']
            except Exception as e:
                logger.debug(f"Service doesn't exist, creating new: {e}")
            
            # Create new service
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
            if 'not idempotent' in str(e).lower() or 'already exists' in str(e).lower():
                logger.warning(f"Service creation conflict, fetching existing service")
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
