import base64
import logging
import os

from aws.container_config import generate_nginx_config, inject_routing_envs
from aws.tags import as_lower_tags

logger = logging.getLogger(__name__)

# The nginx sidecar's own image — exported so tests that exercise the sidecar command
# against a real nginx binary (see api/tests/test_container_config.py and
# api/tests/test_nginx_sidecar_exit_code.py) run the exact image family ECS pulls, not an
# independent guess at "the same family".
NGINX_SIDECAR_IMAGE = 'public.ecr.aws/nginx/nginx:alpine'


class ECSClient:
    def __init__(self, session):
        self.client = session.client('ecs')
        self.health_check_grace_period = int(os.environ.get('ECS_HEALTH_CHECK_GRACE_PERIOD', '240'))
        self.service_stable_timeout = int(os.environ.get('ECS_SERVICE_STABLE_TIMEOUT', '300'))
        self.service_stable_poll_interval = int(os.environ.get('ECS_SERVICE_STABLE_POLL_INTERVAL', '10'))
        self.failed_tasks_threshold = int(os.environ.get('ECS_FAILED_TASKS_THRESHOLD', '3'))
    
    def create_task_definition(self, family, image, cpu, memory, envs, execution_role_arn, container_port=8000, app_name=None, secrets=None, tags=None, host_mode=False, app_hostname=None, log_group=None):
        # H4: callers that already know the log group they're wiring this task
        # definition to (application_deployment_service._create_task_definition) pass
        # it explicitly; the `/ecs/{family}` default only covers a caller that never
        # adopted that lookup (kept so this stays a backward-compatible default, not a
        # breaking signature change).
        log_group = log_group or f'/ecs/{family}'
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
        
        nginx_config = (
            self._generate_nginx_config(app_name, container_port, host_mode=host_mode, app_hostname=app_hostname)
            if app_name else None
        )

        if app_name:
            env_vars = inject_routing_envs(env_vars, app_name, host_mode=host_mode)
        
        container_definitions = []
        
        container_definitions.append({
            'name': family,
            'image': image,
            'essential': True,
            'environment': env_vars,
            **({'secrets': secrets} if secrets else {}),
            'portMappings': [{
                'containerPort': container_port,
                'protocol': 'tcp'
            }],
            'healthCheck': {
                'command': ['CMD-SHELL', f'nc -z 127.0.0.1 {container_port} || exit 1'],
                'interval': 10,
                'timeout': 5,
                'retries': 3,
                'startPeriod': 60,
            },
            'logConfiguration': {
                'logDriver': 'awslogs',
                'options': {
                    'awslogs-group': log_group,
                    'awslogs-region': self.client.meta.region_name,
                    'awslogs-stream-prefix': 'app'
                }
            }
        })
        
        if nginx_config:
            nginx_config_b64 = base64.b64encode(nginx_config.encode()).decode()
            
            container_definitions.append({
                'name': f'{family}-nginx',
                'image': NGINX_SIDECAR_IMAGE,
                'essential': True,
                # No dependsOn: HEALTHY — nginx starts immediately and handles
                # "app not ready" gracefully. The ECS healthCheckGracePeriodSeconds
                # covers the startup window so the ALB doesn't mark the target unhealthy.
                'portMappings': [{
                    'containerPort': 80,
                    'protocol': 'tcp'
                }],
                'environment': [
                    {'name': 'NGINX_CONFIG_B64', 'value': nginx_config_b64}
                ],
                'command': [
                    '/bin/sh', '-c',
                    (
                    # `|| exit 1` (not `&&` into the next statement): a decode failure
                    # must stop the script here too, the same way a failed nginx -t does
                    # below — otherwise it falls through to `nginx -g ...` on a missing or
                    # truncated config, which fails silently in the background just like
                    # the bug this whole command shape fixes.
                    'echo "$NGINX_CONFIG_B64" | base64 -d > /etc/nginx/nginx.conf || exit 1; '
                    # nginx -t is its own statement, run to completion in the foreground
                    # (note the `;`, not `&&`) before anything is backgrounded: the
                    # previous shape, `nginx -t && nginx -g ... &`, backgrounds the whole
                    # test+start chain as one job, so a failing config test only exits
                    # that background job — its `exit 1` never reaches the foreground
                    # script, which sails on into the 180s app-wait loop below and the
                    # monitor loop then exits 0 (nothing left to watch). Ending the `if`
                    # with `;` keeps it a foreground statement: `exit 1` inside it exits
                    # this whole script immediately, before the container ever waits on
                    # an app that has nothing in front of it.
                    'if ! nginx -t; then echo "ERROR: nginx config test failed"; exit 1; fi; '
                    'nginx -g "daemon off;" & '
                    'NGINX_PID=$! && '
                    # Wait for app to be healthy before nginx starts serving real traffic
                    f'for port in {container_port} 8080 8000 3000 5000 4000; do '
                    '  i=0; while [ $i -lt 90 ]; do '
                    '    if nc -z 127.0.0.1 $port 2>/dev/null; then '
                    '      APP_PORT=$port; break 2; '
                    '    fi; '
                    '    sleep 2; i=$((i+1)); '
                    '  done; '
                    'done && '
                    'if [ -z "$APP_PORT" ]; then '
                    '  echo "ERROR: App not reachable on any port after 180s"; kill $NGINX_PID; exit 1; '
                    'fi && '
                    'echo "App ready on port $APP_PORT" && '
                    f'if [ "$APP_PORT" != "{container_port}" ]; then '
                    f'  sed -i "s/127\\.0\\.0\\.1:{container_port}/127.0.0.1:$APP_PORT/g" /etc/nginx/nginx.conf && '
                    '  nginx -s reload; '
                    'fi && '
                    # Monitor app health — kill nginx (and thus the task) if app dies
                    'while kill -0 $NGINX_PID 2>/dev/null; do '
                    '  if ! nc -z 127.0.0.1 $APP_PORT 2>/dev/null; then '
                    '    echo "ERROR: App on port $APP_PORT is no longer reachable — stopping container"; '
                    '    kill $NGINX_PID; exit 1; '
                    '  fi; '
                    '  sleep 15; '
                    'done'
                    )
                ],
                'logConfiguration': {
                    'logDriver': 'awslogs',
                    'options': {
                        'awslogs-group': log_group,
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
            containerDefinitions=container_definitions,
            **({'tags': as_lower_tags(tags)} if tags else {}),
        )
        return response['taskDefinition']['taskDefinitionArn']
    
    def _generate_nginx_config(self, app_name, backend_port, host_mode=False, app_hostname=None):
        return generate_nginx_config(app_name, backend_port, host_mode=host_mode, app_hostname=app_hostname)
    
    def create_service(self, cluster_arn, service_name, task_definition_arn, target_group_arn, subnet_ids, security_group_ids, container_name, container_port=8000, use_nginx=False, tags=None):
        try:
            try:
                response = self.client.describe_services(
                    cluster=cluster_arn,
                    services=[service_name]
                )
                if response['services'] and response['services'][0]['status'] != 'INACTIVE':
                    existing_service = response['services'][0]
                    logger.info(f"Service {service_name} already exists, updating it")

                    # update_service has no `tags` parameter — propagateTags='SERVICE' only
                    # copies the SERVICE resource's *own* tags onto new tasks, so a service
                    # that predates tagging (zero tags of its own) would propagate nothing
                    # no matter how many times it redeploys. tag_resource is what actually
                    # puts launchpad:infra/launchpad:app on the service; only then does
                    # reasserting propagation below make the next task replacement inherit
                    # them, making a redeploy genuinely self-healing.
                    if tags:
                        self.client.tag_resource(
                            resourceArn=existing_service['serviceArn'], tags=as_lower_tags(tags),
                        )

                    self.client.update_service(
                        cluster=cluster_arn,
                        service=service_name,
                        taskDefinition=task_definition_arn,
                        desiredCount=1,
                        forceNewDeployment=True,
                        networkConfiguration={
                            'awsvpcConfiguration': {
                                'subnets': subnet_ids,
                                'securityGroups': security_group_ids,
                                'assignPublicIp': 'DISABLED'
                            }
                        },
                        deploymentConfiguration={
                            'deploymentCircuitBreaker': {'enable': True, 'rollback': True},
                            'maximumPercent': 200,
                            'minimumHealthyPercent': 100,
                        },
                        enableECSManagedTags=True,
                        propagateTags='SERVICE',
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
                deploymentConfiguration={
                    'deploymentCircuitBreaker': {'enable': True, 'rollback': True},
                    'maximumPercent': 200,
                    'minimumHealthyPercent': 100,
                },
                healthCheckGracePeriodSeconds=self.health_check_grace_period,
                # Tasks are where the cost actually lands, and a task is not directly
                # taggable — SERVICE propagation is what makes launchpad:app reach them.
                enableECSManagedTags=True,
                propagateTags='SERVICE',
                **({'tags': as_lower_tags(tags)} if tags else {}),
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
    
    def wait_for_service_stable(self, cluster_arn, service_name, timeout=None, expected_task_definition_arn=None):
        """Wait for the ECS service's PRIMARY deployment to converge.

        Keys on the PRIMARY deployment's own runningCount/desiredCount/rolloutState, not
        the service-wide runningCount/desiredCount: during a rolling update
        (maximumPercent 200, minimumHealthyPercent 100) the previous ACTIVE deployment's
        task can still be running — and still attached to the target group — while the
        new PRIMARY task has already failed and been stopped. At the service level that
        reads as runningCount == desiredCount, falsely "stable", while the deploy this
        call is supposed to be waiting for never actually came up. That is exactly what
        happened on real AWS to e2e-web: the old path-mode task kept serving while the
        new host-mode task's nginx sidecar failed nginx -t, and this check declared
        success anyway. Also requires every non-PRIMARY (ACTIVE) deployment to be fully
        drained — a "converged" PRIMARY next to a still-running old deployment means
        traffic may still be served by the task definition this deploy meant to replace.

        `expected_task_definition_arn` closes a second way the same incident can recur:
        `deploymentCircuitBreaker: {enable: True, rollback: True}` (see create_service/
        update_service below) means ECS itself can give up on our new task definition and
        auto-rollback to the previous one. That rollback is its own new deployment, and
        once IT converges (COMPLETED, runningCount == desiredCount, nothing else running)
        every check above reports "stable" — the caller never finds out its own deploy
        never shipped. Every caller of this method already knows the task definition ARN
        it just told ECS to run, so it's passed in and checked against the PRIMARY
        deployment's own `taskDefinition` before anything is called stable.
        """
        import time
        if timeout is None:
            timeout = self.service_stable_timeout

        logger.info(f"Waiting for service {service_name} to become stable...")
        start_time = time.time()
        last_primary_failed = 0

        while time.time() - start_time < timeout:
            response = self.client.describe_services(
                cluster=cluster_arn,
                services=[service_name]
            )

            if not response['services']:
                raise Exception(f"Service {service_name} not found")

            service = response['services'][0]
            deployments = service.get('deployments', [])
            primary = next((d for d in deployments if d['status'] == 'PRIMARY'), None)

            if primary is None:
                # Plausibly transient immediately after UpdateService/CreateService —
                # ECS hasn't published a deployment list yet. The timeout below still
                # catches a service that never gets one.
                logger.info(f"Service {service_name}: no PRIMARY deployment yet, waiting...")
                time.sleep(self.service_stable_poll_interval)
                continue

            primary_running = primary.get('runningCount', 0)
            primary_desired = primary.get('desiredCount', 0)
            rollout = primary.get('rolloutState', '')
            failed = primary.get('failedTasks', 0)
            last_primary_failed = failed
            primary_task_def = primary.get('taskDefinition')

            if (
                expected_task_definition_arn
                and primary_task_def
                and primary_task_def != expected_task_definition_arn
            ):
                raise Exception(
                    "new version failed and ECS rolled back to the previous one — "
                    "check runtime logs"
                )

            if rollout == 'FAILED':
                raise Exception(
                    f"Service {service_name} deployment failed (circuit breaker triggered). "
                    f"Failed tasks: {failed}. Check CloudWatch logs for the task family."
                )
            if failed >= self.failed_tasks_threshold:
                raise Exception(
                    f"Service {service_name} has {failed} failed tasks — app is crash-looping. "
                    "Check CloudWatch logs for the task family."
                )

            old_deployments_drained = all(
                d.get('runningCount', 0) == 0 for d in deployments if d is not primary
            )
            primary_converged = rollout == 'COMPLETED' or (
                primary_running >= primary_desired and primary_running > 0
            )

            if primary_converged and old_deployments_drained:
                logger.info(
                    f"Service {service_name} is stable: PRIMARY {primary_running}/{primary_desired} "
                    "running, old deployments drained"
                )
                return True

            logger.info(
                f"Service {service_name}: PRIMARY {primary_running}/{primary_desired} running "
                f"(rollout={rollout}), waiting..."
            )
            time.sleep(self.service_stable_poll_interval)

        if last_primary_failed > 0:
            raise Exception("new tasks failed to start — check runtime logs")
        raise Exception(f"Service {service_name} did not become stable within {timeout} seconds")
