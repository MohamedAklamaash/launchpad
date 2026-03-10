import boto3
import logging
import base64
import os

logger = logging.getLogger(__name__)

class ECSClient:
    def __init__(self, session):
        self.client = session.client('ecs')
        self.health_check_grace_period = int(os.environ.get('ECS_HEALTH_CHECK_GRACE_PERIOD', '120'))
        self.service_stable_timeout = int(os.environ.get('ECS_SERVICE_STABLE_TIMEOUT', '300'))
        self.service_stable_poll_interval = int(os.environ.get('ECS_SERVICE_STABLE_POLL_INTERVAL', '10'))
    
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
        
        if nginx_config:
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
                    'echo "Detecting app port..." && '
                    'APP_PORT=0 && '
                    'for i in $(seq 1 60); do '
                    f'  for port in {container_port} 8080 8000 3000 5000; do '
                    '    if nc -z 127.0.0.1 $port 2>/dev/null; then '
                    '      echo "App detected on port $port"; '
                    '      APP_PORT=$port; '
                    '      break 2; '
                    '    fi; '
                    '  done; '
                    '  echo "Waiting for app... ($i/60)"; '
                    '  sleep 3; '
                    'done && '
                    'if [ "$APP_PORT" = "0" ]; then '
                    '  echo "ERROR: App not found on any common port after 180s"; '
                    '  exit 1; '
                    'fi && '
                    'sed -i "s/127\\.0\\.0\\.1:[0-9]*/127.0.0.1:$APP_PORT/g" /etc/nginx/nginx.conf && '
                    'echo "Starting NGINX with backend on port $APP_PORT" && '
                    'nginx -t && nginx -g "daemon off;"'
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
        """Generate NGINX config that strips path prefix"""
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
        server 127.0.0.1:{backend_port} max_fails=3 fail_timeout=30s;
    }}
    
    server {{
        listen 80;
        
        # ALB health check
        location = / {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}
        
        # Redirect /app-name to /app-name/
        location = /{app_name} {{
            return 301 /{app_name}/;
        }}
        
        # Strip prefix and proxy to backend
        location /{app_name}/ {{
            rewrite ^/{app_name}/(.*)$ /$1 break;
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
        }}
    }}
}}
'''
    
    def create_service(self, cluster_arn, service_name, task_definition_arn, target_group_arn, subnet_ids, security_group_ids, container_name, container_port=8000, use_nginx=False):
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
            
            # Route ALB to NGINX container if using sidecar, otherwise to app
            lb_container_name = f"{container_name}-nginx" if use_nginx else container_name
            lb_container_port = 80 if use_nginx else container_port
            
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
                    'containerName': lb_container_name,
                    'containerPort': lb_container_port
                }],
                healthCheckGracePeriodSeconds=self.health_check_grace_period
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
    
    def wait_for_service_stable(self, cluster_arn, service_name, timeout=None):
        """Wait for ECS service to become stable"""
        import time
        if timeout is None:
            timeout = self.service_stable_timeout
        
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
            time.sleep(self.service_stable_poll_interval)
        
        raise Exception(f"Service {service_name} did not become stable within {timeout} seconds")
