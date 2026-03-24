import time
import logging
import re

logger = logging.getLogger(__name__)

class CodeBuildClient:
    def __init__(self, session):
        self._session = session
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
      - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$(echo "$ECR_URL" | cut -d'/' -f1)"
      - |
        if [ -n "$GITHUB_TOKEN" ]; then
          git clone "https://$GITHUB_TOKEN@$(echo "$REPO_URL" | sed 's|https://||')" repo
        else
          git clone "$REPO_URL" repo
        fi
      - cd repo
      - |
        if [ -n "$COMMIT_HASH" ] && [ "$COMMIT_HASH" != "None" ] && [ "$COMMIT_HASH" != "null" ]; then
          git checkout "$COMMIT_HASH" || { echo "ERROR: Commit $COMMIT_HASH not found"; exit 1; }
        else
          git checkout "$BRANCH" || { echo "ERROR: Branch '$BRANCH' not found in repository"; exit 1; }
        fi
  build:
    commands:
      - docker build -f "$DOCKERFILE_PATH" -t "$APP_NAME:latest" .
      - docker tag "$APP_NAME:latest" "$ECR_URL:$APP_NAME-latest"
  post_build:
    commands:
      - docker push "$ECR_URL:$APP_NAME-latest"
      - echo "Image pushed successfully"
'''
    
    def start_build(self, project_name, repo_url, branch, commit_hash, ecr_url, app_name, dockerfile_path="Dockerfile", github_token=None):
        # Sanitize app_name for use as a Docker image tag: lowercase, replace non-[a-z0-9._-] with '-'
        safe_app_name = re.sub(r'[^a-z0-9._-]', '-', app_name.lower()).strip('-')
        max_retries = 3
        retry_delay = 5
        
        env_vars = [
            {'name': 'REPO_URL', 'value': repo_url, 'type': 'PLAINTEXT'},
            {'name': 'BRANCH', 'value': branch, 'type': 'PLAINTEXT'},
            {'name': 'COMMIT_HASH', 'value': commit_hash, 'type': 'PLAINTEXT'},
            {'name': 'ECR_URL', 'value': ecr_url, 'type': 'PLAINTEXT'},
            {'name': 'APP_NAME', 'value': safe_app_name, 'type': 'PLAINTEXT'},
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
            except self.client.exceptions.ResourceNotFoundException:
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
        return {
            'status': build['buildStatus'],
            'phase': build.get('currentPhase', ''),
            'logs': build.get('logs', {}),
        }

    def _get_build_error(self, logs_info):
        """Fetch logs and return the most relevant error lines."""
        try:
            if not (logs_info.get('groupName') and logs_info.get('streamName')):
                return None
            logs = self._session.client('logs')
            paginator = logs.get_paginator('filter_log_events')
            all_messages = []
            for page in paginator.paginate(
                logGroupName=logs_info['groupName'],
                logStreamNames=[logs_info['streamName']],
            ):
                all_messages.extend(e['message'] for e in page.get('events', []))

            # Find error lines
            error_lines = [l for l in all_messages if any(
                kw in l.lower() for kw in ('error', 'fatal', 'failed', 'not found', 'exit status')
            )]
            if error_lines:
                return '\n'.join(error_lines[-10:])
            # Fallback: last 10 non-empty lines
            non_empty = [l for l in all_messages if l.strip()]
            return '\n'.join(non_empty[-10:]) if non_empty else None
        except Exception as e:
            logger.debug(f"Could not fetch build logs: {e}")
            return None
    
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
                log_tail = self._get_build_error(status['logs'])
                error_msg = f"Build failed with status: {status['status']}"

                if log_tail:
                    if 'toomanyrequests' in log_tail.lower() or '429' in log_tail:
                        error_msg = (
                            "Docker Hub rate limit exceeded. "
                            "Update your Dockerfile to use ECR Public Gallery images "
                            "(e.g. public.ecr.aws/docker/library/python:3.11-slim)."
                        )
                    else:
                        error_lines = [l for l in log_tail.splitlines() if l.strip()]
                        error_msg += "\n\n" + '\n'.join(error_lines[-10:])

                deep_link = status['logs'].get('deepLink', '')
                if deep_link:
                    error_msg += f"\n\nFull logs: {deep_link}"

                logger.error(error_msg)
                raise Exception(error_msg)
            
            time.sleep(10)
        
        raise Exception(f"Build timeout after {timeout} seconds")
