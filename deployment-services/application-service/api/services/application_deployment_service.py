import logging
import json
import re
import hashlib
from api.models import Application, Environment
from api.repositories.infrastructure import InfrastructureRepository
from aws.session import create_boto3_session
from aws.codebuild import CodeBuildClient
from aws.ecs import ECSClient
from aws.alb import ALBClient
from aws.ecr import ECRClient
from botocore.exceptions import ClientError
import time

logger = logging.getLogger(__name__)

def _slug(name: str) -> str:
    """Sanitize an app name for use in AWS resource names / Docker tags."""
    return re.sub(r'[^a-z0-9._-]', '-', name.lower()).strip('-')

class ApplicationDeploymentService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()
    
    def deploy_application(self, application: Application):
        created_resources = []
        session = None
        
        try:
            # Step 1: Validate Infrastructure
            environment = self._validate_infrastructure(application)
            
            # Step 2: Assume AWS Role
            session = self._create_aws_session(application.infrastructure)
            
            # Step 3: Trigger Build
            build_id = self._trigger_build(session, application, environment)
            application.build_id = build_id
            application.status = 'BUILDING'
            application.save()
            
            # Step 4: Wait for Build Completion
            self._wait_for_build(session, build_id)
            
            # Step 5: Create ECS Task Definition
            task_def_arn = self._create_task_definition(session, application, environment)
            application.task_definition_arn = task_def_arn
            application.status = 'DEPLOYING'
            application.save()
            created_resources.append(('task_definition', task_def_arn))
            
            # Step 6: Create Target Group
            target_group_arn = self._create_target_group(session, application, environment)
            application.target_group_arn = target_group_arn
            application.save()
            created_resources.append(('target_group', target_group_arn))
            
            # Step 6.5: Configure ALB Routing
            listener_rule_arn, listener_arn = self._configure_alb_routing(session, application, environment)
            application.listener_rule_arn = listener_rule_arn
            application.save()
            created_resources.append(('listener_rule', listener_rule_arn))
            
            # Step 6.6: Verify target group is attached to ALB
            alb = ALBClient(session)
            alb.verify_target_group_attached(application.target_group_arn, listener_arn)
            logger.info("Target group verified as attached to ALB")
            
            # Step 7: Create ECS Service
            service_arn = self._create_ecs_service(session, application, environment)
            application.service_arn = service_arn
            application.desired_count = 1
            application.save()
            created_resources.append(('ecs_service', service_arn))
            
            # Step 8: Wait for service to become stable
            service_name = f"{_slug(application.name)}-service"
            self._wait_for_service_stable_with_refresh(application.infrastructure, environment.cluster_arn, service_name)
            logger.info(f"Service {service_name} is stable and running")
            
            # Step 9: Return Deployment URL
            deployment_url = self._generate_deployment_url(application, environment)
            application.deployment_url = deployment_url
            application.status = 'ACTIVE'
            application.error_message = None
            application.save()
            
            logger.info(f"Application {application.name} deployed successfully at {deployment_url}")
            return deployment_url
            
        except Exception as e:
            logger.error(f"Deployment failed for application {application.name}: {str(e)}", exc_info=True)
            
            if session and created_resources:
                logger.info(f"Cleaning up {len(created_resources)} resources")
                for resource_type, resource_id in reversed(created_resources):
                    try:
                        self._cleanup_resource(session, resource_type, resource_id, environment)
                        logger.info(f"Cleaned up {resource_type}: {resource_id}")
                    except Exception as cleanup_error:
                        logger.error(f"Failed to cleanup {resource_type} {resource_id}: {cleanup_error}")
            
            application.status = 'FAILED'
            application.error_message = str(e)
            application.save()
            raise
    
    def _cleanup_resource(self, session, resource_type, resource_id, environment):
        """Cleanup AWS resources on deployment failure"""
        try:
            if resource_type == 'ecs_service':
                ecs = ECSClient(session)
                service_name = resource_id.split('/')[-1]
                ecs.client.delete_service(
                    cluster=environment.cluster_arn,
                    service=service_name,
                    force=True
                )
            elif resource_type == 'listener_rule':
                alb = ALBClient(session)
                alb.client.delete_rule(RuleArn=resource_id)
            elif resource_type == 'target_group':
                alb = ALBClient(session)
                alb.client.delete_target_group(TargetGroupArn=resource_id)
            elif resource_type == 'task_definition':
                ecs = ECSClient(session)
                ecs.client.deregister_task_definition(taskDefinition=resource_id)
        except ClientError as e:
            if e.response['Error']['Code'] not in ['ResourceNotFoundException', 'TargetGroupNotFound']:
                raise
    
    def _validate_infrastructure(self, application: Application):
        environment = Environment.objects.filter(
            infrastructure=application.infrastructure
        ).first()
        
        if not environment:
            raise ValueError("Infrastructure environment not found")
        
        if environment.status != 'ACTIVE':
            raise ValueError(f"Infrastructure is not active. Current status: {environment.status}")
        
        required_fields = {
            'vpc_id': environment.vpc_id,
            'cluster_arn': environment.cluster_arn,
            'alb_arn': environment.alb_arn,
            'alb_dns': environment.alb_dns,
            'ecr_repository_url': environment.ecr_repository_url,
            'ecs_task_execution_role_arn': environment.ecs_task_execution_role_arn
        }
        
        missing_fields = [field for field, value in required_fields.items() if not value]
        if missing_fields:
            raise ValueError(f"Environment is missing required fields: {', '.join(missing_fields)}")
        
        return environment
    
    def _create_aws_session(self, infrastructure):
        if not infrastructure.code:
            raise ValueError("Infrastructure AWS Account ID (code) is not set")

        if not infrastructure.is_cloud_authenticated:
            raise ValueError("Infrastructure is not authenticated with AWS. Please re-authenticate.")

        # Always refresh STS credentials before a deployment to avoid mid-deploy expiry
        from aws.session import _refresh_credentials
        try:
            _refresh_credentials(infrastructure)
            infrastructure.refresh_from_db()
        except Exception as e:
            logger.warning(f"Credential refresh failed, proceeding with cached credentials: {e}")

        return create_boto3_session(infrastructure)
    
    def _trigger_build(self, session, application: Application, environment: Environment):
        codebuild = CodeBuildClient(session)
        iam = session.client('iam')
        
        project_name = re.sub(r'[^a-zA-Z0-9\-_]', '', f"launchpad-build-{application.infrastructure.id}")
        
        role_name = f"launchpad-codebuild-role-{application.infrastructure.id}"
        try:
            role_response = iam.get_role(RoleName=role_name)
            service_role_arn = role_response['Role']['Arn']
            logger.info(f"Using existing CodeBuild role: {service_role_arn}")
        except iam.exceptions.NoSuchEntityException:
            logger.info(f"Creating CodeBuild service role {role_name}")
            assume_role_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codebuild.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }
            role_response = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy),
                Description="Service role for CodeBuild"
            )
            service_role_arn = role_response['Role']['Arn']
            
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser'
            )
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/CloudWatchLogsFullAccess'
            )
                        
            logger.info("Waiting for IAM role to propagate...")
            time.sleep(15)
            logger.info(f"Created CodeBuild role: {service_role_arn}")
        
        codebuild.ensure_project_exists(project_name, service_role_arn, session.region_name)
        
        dockerfile_path = application.dockerfile_path or "Dockerfile"
        build_context = application.build_context or ""
        
        github_token = None
        try:
            user = application.user
            if user.metadata and 'github' in user.metadata:
                github_token = user.metadata['github'].get('token')
                logger.info("Using GitHub token for private repository access")
        except Exception as e:
            logger.warning(f"Could not get GitHub token: {e}")
        
        build_id = codebuild.start_build(
            project_name=project_name,
            repo_url=application.project_remote_url,
            branch=application.project_branch,
            commit_hash=application.project_commit_hash,
            ecr_url=environment.ecr_repository_url,
            app_name=_slug(application.name),
            dockerfile_path=dockerfile_path,
            build_context=build_context,
            github_token=github_token,
        )
        
        logger.info(f"Started CodeBuild job {build_id} for application {application.name}")
        return build_id
    
    def _wait_for_build(self, session, build_id):
        codebuild = CodeBuildClient(session)
        logger.info(f"Waiting for build {build_id} to complete")
        codebuild.wait_for_build(build_id)
        logger.info(f"Build {build_id} completed successfully")
    
    def _create_task_definition(self, session, application: Application, environment: Environment):
        ecs = ECSClient(session)
        ecr = ECRClient(session)
        logs = session.client('logs')
        
        log_group_name = f"/ecs/{_slug(application.name)}-task"
        try:
            logs.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created log group {log_group_name}")
        except logs.exceptions.ResourceAlreadyExistsException:
            logger.info(f"Log group {log_group_name} already exists")
        
        image_tag = f"{_slug(application.name)}-latest"
        image_uri = ecr.get_image_uri(environment.ecr_repository_url, image_tag)
        
        envs = {**(application.envs or {}), 'PORT': str(application.port)}
        
        task_def_arn = ecs.create_task_definition(
            family=f"{_slug(application.name)}-task",
            image=image_uri,
            cpu=application.alloted_cpu,
            memory=application.alloted_memory,
            envs=envs,
            execution_role_arn=environment.ecs_task_execution_role_arn,
            container_port=application.port,
            app_name=_slug(application.name)
        )
        
        logger.info(f"Created task definition {task_def_arn}")
        return task_def_arn
    
    def _create_target_group(self, session, application: Application, environment: Environment):
        alb = ALBClient(session)

        if application.target_group_arn:
            try:
                resp = alb.client.describe_target_groups(TargetGroupArns=[application.target_group_arn])
                tg = resp['TargetGroups'][0]
                if tg['VpcId'] == environment.vpc_id:
                    logger.info(f"Reusing existing target group {application.target_group_arn}")
                    return application.target_group_arn
                else:
                    logger.warning(f"Stored TG is in wrong VPC ({tg['VpcId']} != {environment.vpc_id}), deleting and creating new one")
                    try:
                        alb.client.delete_target_group(TargetGroupArn=application.target_group_arn)
                    except ClientError:
                        pass
            except ClientError as e:
                if e.response['Error']['Code'] != 'TargetGroupNotFound':
                    raise
                logger.info("Stored TG ARN no longer exists, creating new one")

        # Include infra ID suffix to prevent name collisions across infrastructures
        infra_suffix = str(application.infrastructure_id)[:8]
        tg_name = f"{_slug(application.name)}-{infra_suffix}-tg"[:32]
        target_group_arn = alb.create_target_group(name=tg_name, vpc_id=environment.vpc_id, port=80)
        logger.info(f"Created target group {target_group_arn}")
        return target_group_arn
    
    def _get_app_sg_name(self, application: Application) -> str:
        suffix = hashlib.md5(str(application.infrastructure_id).encode()).hexdigest()[:8]
        return f"infra-{str(application.infrastructure_id)[:8]}-{suffix}-fargate-sg"

    def _get_or_create_app_security_group(self, ec2, application: Application, environment: Environment, alb_sg_id: str) -> str:
        sg_name = self._get_app_sg_name(application)

        existing = ec2.describe_security_groups(
            Filters=[
                {'Name': 'vpc-id', 'Values': [environment.vpc_id]},
                {'Name': 'group-name', 'Values': [sg_name]}
            ]
        )['SecurityGroups']

        if existing:
            sg_id = existing[0]['GroupId']
            logger.info(f"Reusing existing app security group {sg_name} ({sg_id})")
        else:
            sg_id = ec2.create_security_group(
                GroupName=sg_name,
                Description=f"Security group for app {application.name} in infra {application.infrastructure_id}",
                VpcId=environment.vpc_id
            )['GroupId']
            logger.info(f"Created app security group {sg_name} ({sg_id})")

        for port in {80, application.port}:
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[{
                        'IpProtocol': 'tcp',
                        'FromPort': port,
                        'ToPort': port,
                        'UserIdGroupPairs': [{'GroupId': alb_sg_id}]
                    }]
                )
            except ClientError as e:
                if 'InvalidPermission.Duplicate' not in str(e):
                    raise

        return sg_id

    def _create_ecs_service(self, session, application: Application, environment: Environment):
        ecs = ECSClient(session)
        ec2 = session.client('ec2')
        
        # Get private subnets
        try:
            vpc_response = ec2.describe_subnets(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [environment.vpc_id]},
                    {'Name': 'tag:Type', 'Values': ['private']}
                ]
            )
            subnet_ids = [subnet['SubnetId'] for subnet in vpc_response['Subnets']]
            
            if not subnet_ids:
                logger.warning("No private subnets found with Type tag, using all subnets")
                vpc_response = ec2.describe_subnets(
                    Filters=[{'Name': 'vpc-id', 'Values': [environment.vpc_id]}]
                )
                subnet_ids = [subnet['SubnetId'] for subnet in vpc_response['Subnets']]
            
            if not subnet_ids:
                raise ValueError(f"No subnets found in VPC {environment.vpc_id}")
            
            logger.info(f"Using subnets: {subnet_ids}")
        except Exception as e:
            logger.error(f"Failed to get subnets: {e}")
            raise ValueError(f"Failed to get subnets from VPC: {e}")
        
        try:
            alb_sg_id = environment.alb_security_group_id
            if not alb_sg_id:
                raise ValueError("ALB security group ID not found on environment")

            app_sg_id = self._get_or_create_app_security_group(ec2, application, environment, alb_sg_id)
            security_group_ids = [app_sg_id]
            logger.info(f"Using app-specific security group: {app_sg_id}")
        except Exception as e:
            logger.error(f"Failed to configure security groups: {e}")
            raise ValueError(f"Failed to configure security groups: {e}")
        
        service_arn = ecs.create_service(
            cluster_arn=environment.cluster_arn,
            service_name=f"{_slug(application.name)}-service",
            task_definition_arn=application.task_definition_arn,
            target_group_arn=application.target_group_arn,
            subnet_ids=subnet_ids,
            security_group_ids=security_group_ids,
            container_name=f"{_slug(application.name)}-task",
            container_port=application.port,
            use_nginx=True
        )
        
        logger.info(f"Created ECS service {service_arn}")
        return service_arn
    
    def _configure_alb_routing(self, session, application: Application, environment: Environment):
        alb = ALBClient(session)
        
        listener_arn = alb.get_listener_arn(environment.alb_arn)
        if not listener_arn:
            raise ValueError("No listener found for ALB")

        if application.listener_rule_arn:
            try:
                alb.client.delete_rule(RuleArn=application.listener_rule_arn)
                logger.info(f"Deleted old listener rule {application.listener_rule_arn}")
            except ClientError as e:
                if e.response['Error']['Code'] == 'RuleNotFound':
                    logger.info(f"Old listener rule {application.listener_rule_arn} was already absent")
                else:
                    logger.error(f"Could not delete old listener rule {application.listener_rule_arn}: {e}")
                    raise

        priority = alb.get_next_priority(listener_arn)
        
        listener_rule_arn = alb.create_listener_rule(
            listener_arn=listener_arn,
            target_group_arn=application.target_group_arn,
            path_pattern=f"/{_slug(application.name)}*",
            priority=priority
        )
        
        logger.info(f"Created listener rule {listener_rule_arn}")
        return listener_rule_arn, listener_arn
    
    def _generate_deployment_url(self, application: Application, environment: Environment):
        return f"http://{environment.alb_dns}/{_slug(application.name)}"
    
    def _wait_for_service_stable_with_refresh(self, infrastructure, cluster_arn, service_name, timeout=300):
        """Wait for ECS service with automatic credential refresh"""
        logger.info(f"Waiting for service {service_name} to become stable...")
        start_time = time.time()
        session = self._create_aws_session(infrastructure)
        ecs = ECSClient(session)

        while time.time() - start_time < timeout:
            try:
                response = ecs.client.describe_services(
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
                time.sleep(10)

            except Exception as e:
                if 'ExpiredToken' in str(e):
                    logger.warning("Token expired during wait, refreshing credentials")
                    session = self._create_aws_session(infrastructure)
                    ecs = ECSClient(session)
                else:
                    raise

        raise Exception(f"Service {service_name} did not become stable within {timeout} seconds")
