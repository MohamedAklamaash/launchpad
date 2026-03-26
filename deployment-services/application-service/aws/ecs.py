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
        
        nginx_config = self._generate_nginx_config(app_name, container_port) if app_name else None
        
        if app_name:
            env_vars = [e for e in env_vars if e['name'] not in ('ROOT_PATH', 'UVICORN_ROOT_PATH', 'FORWARDED_ALLOW_IPS')]
            env_vars += [
                {'name': 'ROOT_PATH', 'value': f'/{app_name}'},
                {'name': 'UVICORN_ROOT_PATH', 'value': f'/{app_name}'},
                {'name': 'FORWARDED_ALLOW_IPS', 'value': '*'},
            ]
        
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
                    'nginx -t && nginx -g "daemon off;" & '
                    'NGINX_PID=$! && '
                    'APP_PORT=0 && '
                    f'for port in {container_port} 8080 8000 3000 5000 4000; do '
                    '  i=0; while [ $i -lt 90 ]; do '
                    '    if wget -q -O /dev/null "http://127.0.0.1:$port/" 2>/dev/null || '
                    '       wget -q -O /dev/null "http://127.0.0.1:$port/health" 2>/dev/null; then '
                    '      APP_PORT=$port; break 2; '
                    '    fi; '
                    '    sleep 2; i=$((i+1)); '
                    '  done; '
                    'done && '
                    'if [ "$APP_PORT" = "0" ]; then '
                    '  echo "ERROR: App not reachable on any port after 180s"; kill $NGINX_PID; exit 1; '
                    'fi && '
                    'echo "App ready on port $APP_PORT" && '
                    f'if [ "$APP_PORT" != "{container_port}" ]; then '
                    f'  sed -i "s/127\\.0\\.0\\.1:{container_port}/127.0.0.1:$APP_PORT/g" /etc/nginx/nginx.conf && '
                    '  nginx -s reload; '
                    'fi && '
                    'wait $NGINX_PID'
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
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    client_max_body_size 100m;

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
            proxy_set_header X-Forwarded-Prefix /{app_name};
            proxy_http_version 1.1;
            # WebSocket support
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_buffering off;
        }}
    }}

    # WebSocket connection upgrade map
    map $http_upgrade $connection_upgrade {{
        default upgrade;
        ''      close;
    }}
}}
'''
    
    def create_service(self, cluster_arn, service_name, task_definition_arn, target_group_arn, subnet_ids, security_group_ids, container_name, container_port=8000, use_nginx=False):
        try:
            try:
                response = self.client.describe_services(
                    cluster=cluster_arn,
                    services=[service_name]
                )
                if response['services'] and response['services'][0]['status'] != 'INACTIVE':
                    existing_service = response['services'][0]
                    logger.info(f"Service {service_name} already exists, updating it")
                    
                    self.client.update_service(
                        cluster=cluster_arn,
                        service=service_name,
                        taskDefinition=task_definition_arn,
                        desiredCount=1,
                        forceNewDeployment=True,
                    )
                    return existing_service['serviceArn']
            except Exception as e:
                logger.debug(f"Service doesn't exist, creating new: {e}")
            
            lb_container_name = f"{container_name}-nginx" if use_nginx else container_name
            lb_container_port = 80 if use_nginx else container_port
            
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
                logger.warning("Service creation conflict, fetching existing service")
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
            
            running_count = service.get('runningCount', 0)
            desired_count = service.get('desiredCount', 0)
            
            if running_count == desired_count and running_count > 0:
                logger.info(f"Service {service_name} is stable with {running_count} running tasks")
                return True
            
            logger.info(f"Service {service_name}: {running_count}/{desired_count} tasks running, waiting...")
            time.sleep(self.service_stable_poll_interval)
        
        raise Exception(f"Service {service_name} did not become stable within {timeout} seconds")
