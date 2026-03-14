import logging
import json
from api.models import Application, Environment
from api.repositories.infrastructure import InfrastructureRepository
from aws.session import create_boto3_session
from aws.codebuild import CodeBuildClient
from aws.ecs import ECSClient
from aws.alb import ALBClient
from aws.ecr import ECRClient
from django.db import transaction
from botocore.exceptions import ClientError
import time

logger = logging.getLogger(__name__)

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
            service_name = f"{application.name}-service"
            self._wait_for_service_stable_with_refresh(application.infrastructure, environment.cluster_arn, service_name)
            logger.info(f"Service {service_name} is stable and running")
            
            # Step 9: Return Deployment URL
            deployment_url = self._generate_deployment_url(application, environment)
            application.deployment_url = deployment_url
            application.status = 'ACTIVE'
            application.save()
            
            logger.info(f"Application {application.name} deployed successfully at {deployment_url}")
            return deployment_url
            
        except Exception as e:
            logger.error(f"Deployment failed for application {application.name}: {str(e)}", exc_info=True)
            
            # Cleanup created resources in reverse order
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
        
        # Validate required environment fields
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
        
        return create_boto3_session(infrastructure)
    
    def _trigger_build(self, session, application: Application, environment: Environment):
        codebuild = CodeBuildClient(session)
        ecr = ECRClient(session)
        iam = session.client('iam')
        
        project_name = f"launchpad-build-{application.infrastructure.id}"
        
        # Get or create CodeBuild service role
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
            
            # Attach necessary policies
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser'
            )
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/CloudWatchLogsFullAccess'
            )
                        
            logger.info("Waiting for IAM role to propagate...")
            time.sleep(10)
            logger.info(f"Created CodeBuild role: {service_role_arn}")
        
        # Ensure CodeBuild project exists
        codebuild.ensure_project_exists(project_name, service_role_arn, session.region_name)
        
        image_tag = f"{application.name}-{application.version}"
        dockerfile_path = application.dockerfile_path or "Dockerfile"
        
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
            app_name=application.name,
            dockerfile_path=dockerfile_path,
            github_token=github_token
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
        
        # Create CloudWatch log group
        log_group_name = f"/ecs/{application.name}-task"
        try:
            logs.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created log group {log_group_name}")
        except logs.exceptions.ResourceAlreadyExistsException:
            logger.info(f"Log group {log_group_name} already exists")
        
        image_tag = f"{application.name}-latest"
        image_uri = ecr.get_image_uri(environment.ecr_repository_url, image_tag)
        
        envs = {**(application.envs or {}), 'PORT': str(application.port)}
        
        task_def_arn = ecs.create_task_definition(
            family=f"{application.name}-task",
            image=image_uri,
            cpu=application.alloted_cpu,
            memory=application.alloted_memory,
            envs=envs,
            execution_role_arn=environment.ecs_task_execution_role_arn,
            container_port=application.port,
            app_name=application.name
        )
        
        logger.info(f"Created task definition {task_def_arn}")
        return task_def_arn
    
    def _create_target_group(self, session, application: Application, environment: Environment):
        alb = ALBClient(session)
        
        # Check if target group already exists
        if application.target_group_arn:
            try:
                alb.client.describe_target_groups(TargetGroupArns=[application.target_group_arn])
                logger.info(f"Target group {application.target_group_arn} already exists, reusing")
                return application.target_group_arn
            except ClientError as e:
                if e.response['Error']['Code'] != 'TargetGroupNotFound':
                    raise
                logger.info("Target group ARN stored but resource doesn't exist, creating new one")
        
        tg_name = f"{application.name}-tg"[:32]
        # Use port 80 for NGINX sidecar
        target_group_arn = alb.create_target_group(
            name=tg_name,
            vpc_id=environment.vpc_id,
            port=80
        )
        
        logger.info(f"Created target group {target_group_arn}")
        return target_group_arn
    
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
            alb_sg_response = ec2.describe_security_groups(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [environment.vpc_id]},
                    {'Name': 'group-name', 'Values': ['*alb-sg*']}
                ]
            )
            
            if not alb_sg_response['SecurityGroups']:
                alb_sg_response = ec2.describe_security_groups(
                    Filters=[{'Name': 'vpc-id', 'Values': [environment.vpc_id]}]
                )
            
            security_group_ids = [sg['GroupId'] for sg in alb_sg_response['SecurityGroups']]
            alb_sg_id = security_group_ids[0] if security_group_ids else None
            
            if not security_group_ids:
                raise ValueError(f"No security groups found in VPC {environment.vpc_id}")
            
            # Add ingress rule to allow traffic from ALB to NGINX port 80
            if alb_sg_id:
                try:
                    ec2.authorize_security_group_ingress(
                        GroupId=alb_sg_id,
                        IpPermissions=[{
                            'IpProtocol': 'tcp',
                            'FromPort': 80,
                            'ToPort': 80,
                            'UserIdGroupPairs': [{'GroupId': alb_sg_id}]
                        }]
                    )
                    logger.info(f"Added ingress rule for port 80 to security group {alb_sg_id}")
                except ec2.exceptions.ClientError as e:
                    if 'InvalidPermission.Duplicate' in str(e):
                        logger.info(f"Ingress rule for port 80 already exists")
                    else:
                        raise
            
            logger.info(f"Using security groups: {security_group_ids}")
        except Exception as e:
            logger.error(f"Failed to configure security groups: {e}")
            raise ValueError(f"Failed to configure security groups: {e}")
        
        service_arn = ecs.create_service(
            cluster_arn=environment.cluster_arn,
            service_name=f"{application.name}-service",
            task_definition_arn=application.task_definition_arn,
            target_group_arn=application.target_group_arn,
            subnet_ids=subnet_ids,
            security_group_ids=security_group_ids,
            container_name=f"{application.name}-task",
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
        
        priority = alb.get_next_priority(listener_arn)
        
        listener_rule_arn = alb.create_listener_rule(
            listener_arn=listener_arn,
            target_group_arn=application.target_group_arn,
            path_pattern=f"/{application.name}*",
            priority=priority
        )
        
        logger.info(f"Created listener rule {listener_rule_arn}")
        return listener_rule_arn, listener_arn
    
    def _generate_deployment_url(self, application: Application, environment: Environment):
        return f"http://{environment.alb_dns}/{application.name}"
    
    def _wait_for_service_stable_with_refresh(self, infrastructure, cluster_arn, service_name, timeout=300):
        """Wait for ECS service with automatic credential refresh"""
        import time
        logger.info(f"Waiting for service {service_name} to become stable...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Refresh session on each iteration to handle credential expiry
                session = self._create_aws_session(infrastructure)
                ecs = ECSClient(session)
                
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
                    logger.warning(f"Token expired during wait, will refresh on next iteration")
                    time.sleep(2)
                else:
                    raise
        
        raise Exception(f"Service {service_name} did not become stable within {timeout} seconds")
