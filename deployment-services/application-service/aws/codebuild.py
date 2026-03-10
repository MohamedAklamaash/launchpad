import boto3
import time
import logging

logger = logging.getLogger(__name__)

class CodeBuildClient:
    def __init__(self, session):
        self.client = session.client('codebuild')
    
    def ensure_project_exists(self, project_name, service_role_arn, region):
        try:
            response = self.client.batch_get_projects(names=[project_name])
            if response.get('projects'):
                logger.info(f"CodeBuild project {project_name} already exists")
                return
        except Exception as e:
            logger.info(f"CodeBuild project {project_name} does not exist, will create: {e}")
        
        logger.info(f"Creating CodeBuild project {project_name}")
        try:
            self.client.create_project(
                name=project_name,
                source={
                    'type': 'NO_SOURCE',
                    'buildspec': self._get_buildspec()
                },
                artifacts={'type': 'NO_ARTIFACTS'},
                environment={
                    'type': 'LINUX_CONTAINER',
                    'image': 'aws/codebuild/standard:7.0',
                    'computeType': 'BUILD_GENERAL1_SMALL',
                    'privilegedMode': True,
                    'environmentVariables': []
                },
                serviceRole=service_role_arn,
                logsConfig={
                    'cloudWatchLogs': {
                        'status': 'ENABLED',
                        'groupName': f'/aws/codebuild/{project_name}'
                    }
                }
            )
            logger.info(f"Successfully created CodeBuild project {project_name}")
        except self.client.exceptions.ResourceAlreadyExistsException:
            logger.info(f"CodeBuild project {project_name} already exists (race condition)")
        except Exception as e:
            logger.error(f"Failed to create CodeBuild project {project_name}: {e}")
            raise
    
    def _get_buildspec(self):
        return '''version: 0.2
phases:
  pre_build:
    commands:
      - echo "Build started on $(date)"
      - echo "Logging in to Amazon ECR..."
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $(echo $ECR_URL | cut -d'/' -f1)
      - echo "Cloning repository $REPO_URL..."
      - |
        if [ -n "$GITHUB_TOKEN" ]; then
          git clone https://$GITHUB_TOKEN@$(echo $REPO_URL | sed 's|https://||') repo
        else
          git clone $REPO_URL repo
        fi
      - cd repo
      - echo "Checking out $BRANCH at $COMMIT_HASH..."
      - git checkout $COMMIT_HASH || git checkout $BRANCH
      - ls -la
  build:
    commands:
      - echo "Building Docker image from $DOCKERFILE_PATH..."
      - docker build -f $DOCKERFILE_PATH -t $APP_NAME:latest . || exit 1
      - echo "Tagging image..."
      - docker tag $APP_NAME:latest $ECR_URL:$APP_NAME-latest || exit 1
  post_build:
    commands:
      - echo "Build completed on $(date)"
      - echo "Pushing Docker image to ECR..."
      - docker push $ECR_URL:$APP_NAME-latest || exit 1
      - echo "Image pushed successfully"
'''
    
    def start_build(self, project_name, repo_url, branch, commit_hash, ecr_url, app_name, dockerfile_path="Dockerfile", github_token=None):
        import time
        max_retries = 3
        retry_delay = 5
        
        env_vars = [
            {'name': 'REPO_URL', 'value': repo_url, 'type': 'PLAINTEXT'},
            {'name': 'BRANCH', 'value': branch, 'type': 'PLAINTEXT'},
            {'name': 'COMMIT_HASH', 'value': commit_hash, 'type': 'PLAINTEXT'},
            {'name': 'ECR_URL', 'value': ecr_url, 'type': 'PLAINTEXT'},
            {'name': 'APP_NAME', 'value': app_name, 'type': 'PLAINTEXT'},
            {'name': 'DOCKERFILE_PATH', 'value': dockerfile_path, 'type': 'PLAINTEXT'},
        ]
        
        # Add GitHub token (for private repos)
        if github_token:
            env_vars.append({'name': 'GITHUB_TOKEN', 'value': github_token, 'type': 'PLAINTEXT'})
        
        for attempt in range(max_retries):
            try:
                response = self.client.start_build(
                    projectName=project_name,
                    environmentVariablesOverride=env_vars
                )
                return response['build']['id']
            except self.client.exceptions.ResourceNotFoundException as e:
                if attempt < max_retries - 1:
                    logger.warning(f"CodeBuild project not found (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"CodeBuild project {project_name} not found after {max_retries} attempts")
                    raise
            except Exception as e:
                logger.error(f"Failed to start build: {e}")
                raise
    
    def get_build_status(self, build_id):
        response = self.client.batch_get_builds(ids=[build_id])
        if not response['builds']:
            return None
        build = response['builds'][0]
        
        # Try to fetch recent logs to detect specific errors
        log_tail = None
        try:
            logs_info = build.get('logs', {})
            if logs_info.get('groupName') and logs_info.get('streamName'):
                logs_client = self.client._client_config.__dict__.get('_user_provided_options', {}).get('region_name')
                import boto3
                logs = boto3.client('logs', region_name=self.client.meta.region_name)
                
                log_response = logs.get_log_events(
                    logGroupName=logs_info['groupName'],
                    logStreamName=logs_info['streamName'],
                    limit=50,
                    startFromHead=False
                )
                
                if log_response.get('events'):
                    log_tail = '\n'.join([e['message'] for e in log_response['events'][-10:]])
        except Exception as e:
            logger.debug(f"Could not fetch log tail: {e}")
        
        return {
            'status': build['buildStatus'],
            'phase': build.get('currentPhase', ''),
            'logs': build.get('logs', {}).get('deepLink', ''),
            'log_tail': log_tail
        }
    
    def wait_for_build(self, build_id, timeout=1800):
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_build_status(build_id)
            if not status:
                raise Exception(f"Build {build_id} not found")
            
            logger.info(f"Build {build_id} status: {status['status']}, phase: {status['phase']}")
            
            if status['status'] == 'SUCCEEDED':
                return True
            elif status['status'] in ['FAILED', 'FAULT', 'TIMED_OUT', 'STOPPED']:
                error_msg = f"Build failed with status: {status['status']}"
                if status.get('logs'):
                    error_msg += f"\nLogs: {status['logs']}"
                
                # Check log content for specific errors
                log_tail = status.get('log_tail', '')
                if log_tail:
                    if '429 Too Many Requests' in log_tail or 'toomanyrequests' in log_tail.lower():
                        error_msg += (
                            "\n\n Docker Hub Rate Limit Exceeded!"
                            "\n\nYour Dockerfile is pulling from Docker Hub (docker.io) which has rate limits."
                            "\n\nFix: Update your Dockerfile to use ECR Public Gallery:"
                            "\n  FROM python:3.11-slim"
                            "\n FROM public.ecr.aws/docker/library/python:3.11-slim"
                            "\n\n FROM node:21-alpine"
                            "\n FROM public.ecr.aws/docker/library/node:21-alpine"
                            "\n\n FROM nginx:alpine"
                            "\n FROM public.ecr.aws/docker/library/nginx:alpine"
                        )
                
                logger.error(error_msg)
                raise Exception(error_msg)
            
            time.sleep(10)
        
        raise Exception(f"Build timeout after {timeout} seconds")
