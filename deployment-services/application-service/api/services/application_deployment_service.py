import json
import logging
import re
import time

from aws.alb import ALBClient
from aws.codebuild import CodeBuildClient
from aws.container_config import HOST_MODE_HEALTH_CHECK_PATH
from aws.ecr import ECRClient
from aws.ecs import ECSClient
from aws.session import create_boto3_session
from aws.tags import app_tags, as_key_value_tags, infra_tags
from aws.tags import target_group_tags as _target_group_tags
from botocore.exceptions import ClientError
from shared.aws.app_security_group import (
    app_security_group_name,
)
from shared.aws.app_security_group import (
    get_or_create_app_security_group as _shared_get_or_create_app_sg,
)
from shared.enums.orchestrator import ComputeType
from shared.errors.deploy_errors import sanitize_deploy_error

from api.common.host_url import (
    HostUrlNotAvailable,
    build_app_hostname,
    infra_host_ready,
)
from api.common.naming import app_slug as _slug
from api.common.naming import ecs_log_group as _legacy_ecs_log_group
from api.common.naming import ecs_task_family as _legacy_ecs_task_family
from api.common.naming import ecs_task_family_for as _ecs_task_family_for
from api.common.naming import image_tag as _image_tag
from api.common.naming import new_ecs_log_group as _new_ecs_log_group
from api.common.naming import new_ecs_task_family as _new_ecs_task_family
from api.common.naming import target_group_name as _target_group_name
from api.k8s.deployer import EKSDeployer
from api.models import Application, Environment
from api.repositories.infrastructure import InfrastructureRepository

logger = logging.getLogger(__name__)

ECS_REQUIRED_ENVIRONMENT_FIELDS = (
    'vpc_id', 'cluster_arn', 'alb_arn', 'alb_dns', 'ecr_repository_url', 'ecs_task_execution_role_arn',
)
EKS_REQUIRED_ENVIRONMENT_FIELDS = ('vpc_id', 'cluster_arn', 'ecr_repository_url', 'alb_dns')


def _is_eks(application: Application) -> bool:
    return application.infrastructure.compute_type == ComputeType.EKS

class ApplicationDeploymentService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()
    
    def deploy_application(self, application: Application):
        created_resources = []
        session = None
        # Bound up front so the except block below can tell "a build was attempted" from
        # "failed before we even knew what we'd be building" — only the former is worth a
        # Deployment history row.
        image_tag = None
        resolved_sha = None

        try:
            # Step 1: Validate Infrastructure
            environment = self._validate_infrastructure(application)

            # Step 2: Assume AWS Role
            session = self._create_aws_session(application.infrastructure)

            # Step 2.5: Resolve routing mode BEFORE the image is even built — host mode
            # bakes the app's hostname into the nginx sidecar config inside the task
            # definition, so it has to be known before Step 5 registers it. Never fails
            # the deploy: an app that isn't eligible for host mode this time simply
            # deploys in path mode, exactly as it always has (F1b part 3a is additive).
            host_mode, app_hostname, _host_reason = (
                self._resolve_host_routing(application, environment, session)
                if not _is_eks(application) else (False, None, "eks")
            )

            # Step 3: Trigger Build
            image_tag = _image_tag(application)
            build_id = self._trigger_build(session, application, environment, image_tag)
            application.build_id = build_id
            application.status = 'BUILDING'
            application.save()

            # Step 4: Wait for Build Completion
            resolved_sha = self._wait_for_build(session, build_id)

            if _is_eks(application):
                pinned_tag = self._pinned_image_tag(application, resolved_sha)
                return self._deploy_to_eks(
                    session, application, environment, pinned_tag, created_resources,
                    resolved_sha=resolved_sha,
                )


            # Step 5: Create ECS Task Definition
            task_def_arn = self._create_task_definition(
                session, application, environment, resolved_sha=resolved_sha,
                host_mode=host_mode, app_hostname=app_hostname,
            )
            application.task_definition_arn = task_def_arn
            application.status = 'DEPLOYING'
            application.save()
            created_resources.append(('task_definition', task_def_arn))

            # Step 6: Create Target Group — health check path moves in lockstep with the
            # nginx sidecar's own routing mode (see container_config.HOST_MODE_HEALTH_CHECK_PATH),
            # whether the target group is newly created or reused from a previous deploy.
            health_check_path = HOST_MODE_HEALTH_CHECK_PATH if host_mode else '/'
            target_group_arn = self._create_target_group(
                session, application, environment, health_check_path=health_check_path,
            )
            application.target_group_arn = target_group_arn
            application.save()
            created_resources.append(('target_group', target_group_arn))

            # Step 7-8.65: Create the ECS service and wire up ALB routing, in whichever
            # order the target group's current attachment to the ALB requires — see
            # _create_ecs_service_with_routing.
            listener_arn = self._create_ecs_service_with_routing(
                session, application, environment, host_mode, app_hostname, created_resources,
            )

            # Step 8.7: Verify target group is attached to ALB
            alb = ALBClient(session)
            alb.verify_target_group_attached(application.target_group_arn, listener_arn)
            logger.info("Target group verified as attached to ALB")

            # Step 9: Return Deployment URL
            deployment_url = self._generate_deployment_url(application, environment)
            application.deployment_url = deployment_url
            application.status = 'ACTIVE'
            application.error_message = None
            application.save()

            self._record_deployment(
                application, image_tag=self._pinned_image_tag(application, resolved_sha),
                resolved_sha=resolved_sha, status='SUCCEEDED', triggered_by='DEPLOY',
                session=session, environment=environment,
            )
            logger.info(f"Application {application.name} deployed successfully at {deployment_url}")
            return deployment_url

        except Exception as e:
            logger.exception(f"Deployment failed for application {application.name}")

            if session and created_resources:
                logger.info(f"Cleaning up {len(created_resources)} resources")
                for resource_type, resource_id in reversed(created_resources):
                    try:
                        self._cleanup_resource(session, resource_type, resource_id, application, environment)
                        logger.info(f"Cleaned up {resource_type}: {resource_id}")
                    except Exception as cleanup_error:
                        logger.error(f"Failed to cleanup {resource_type} {resource_id}: {cleanup_error}")

            application.status = 'FAILED'
            # Served back over the API and rendered in the dashboard, so the exception
            # cannot go in raw: a boto3 AccessDenied names the platform's own IAM user,
            # and a Kubernetes ApiException carries the whole HTTP response.
            application.error_message = sanitize_deploy_error(e)
            application.save()

            if image_tag:
                # The build may have already resolved a SHA even though a later step (ECS/EKS
                # resource creation) is what actually failed — record the tag it will pin to,
                # not the pre-build placeholder, so tag_source and image_tag stay consistent.
                failed_tag = self._pinned_image_tag(application, resolved_sha) if resolved_sha else image_tag
                self._record_deployment(
                    application, image_tag=failed_tag, resolved_sha=resolved_sha,
                    status='FAILED', triggered_by='DEPLOY',
                )
            raise
    
    def _cleanup_resource(self, session, resource_type, resource_id, application, environment):
        """Cleanup AWS resources on deployment failure"""
        try:
            if resource_type == 'k8s_object':
                EKSDeployer(session, application, environment).delete_object(resource_id)
            elif resource_type == 'ecs_service':
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
            elif resource_type == 'host_forward_rule':
                # R2: host_forward_rule_arn is saved to the DB before
                # verify_target_group_attached runs (see _configure_host_routing) — a
                # failure there must not leave a stale ARN pointing at a rule this branch
                # just deleted, or app_host_url() would keep reporting a live host route
                # that no longer exists. Cleared even if the rule was already gone
                # (RuleNotFound) — either way, nothing this ARN names is still live.
                alb = ALBClient(session)
                try:
                    alb.client.delete_rule(RuleArn=resource_id)
                except ClientError as e:
                    if e.response['Error']['Code'] != 'RuleNotFound':
                        raise
                application.host_forward_rule_arn = None
                Application.objects.filter(id=application.id).update(host_forward_rule_arn=None)
            elif resource_type == 'target_group':
                alb = ALBClient(session)
                alb.client.delete_target_group(TargetGroupArn=resource_id)
            elif resource_type == 'task_definition':
                ecs = ECSClient(session)
                ecs.client.deregister_task_definition(taskDefinition=resource_id)
        except ClientError as e:
            if e.response['Error']['Code'] not in ['ResourceNotFoundException', 'TargetGroupNotFound']:
                raise
    
    def _deploy_to_eks(self, session, application: Application, environment: Environment,
                       image_tag: str, created_resources: list, resolved_sha: str | None = None):
        ecr = ECRClient(session)
        image_uri = ecr.get_image_uri(environment.ecr_repository_url, image_tag)

        application.status = 'DEPLOYING'
        application.save()

        self._abort_if_exited(application)
        EKSDeployer(session, application, environment).deploy(image_uri, created_resources)

        deployment_url = self._generate_deployment_url(application, environment)
        application.deployment_url = deployment_url
        application.status = 'ACTIVE'
        application.error_message = None
        application.save()

        self._record_deployment(
            application, image_tag=image_tag, resolved_sha=resolved_sha,
            status='SUCCEEDED', triggered_by='DEPLOY',
            session=session, environment=environment,
        )
        logger.info(f"Application {application.name} deployed to EKS at {deployment_url}")
        return deployment_url

    def _pinned_image_tag(self, application: Application, resolved_sha: str | None) -> str:
        """Pin to the immutable per-commit tag the buildspec exports; fall back to the
        moving -latest tag only for a CodeBuild project that predates the two-tag buildspec.
        Used for both compute types so `Deployment.commit_sha` means the same thing on
        either — before this, EKS pinned to the *requested* commit (chosen pre-build) while
        ECS pinned to what the build actually resolved, and rollback needs the two to agree.
        """
        slug = _slug(application.name)
        return f"{slug}-{resolved_sha}" if resolved_sha else f"{slug}-latest"

    def _record_deployment(self, application: Application, image_tag: str, resolved_sha: str | None,
                           status: str, triggered_by: str, tag_source: str | None = None,
                           rolled_back_from=None, image_digest: str | None = None,
                           session=None, environment: Environment | None = None):
        """Append-only deploy history. Snapshot the env's shape, never its values.
        Best-effort — a history row must never be the reason a deploy or rollback that
        actually succeeded gets reported as failed.

        `image_digest`: pass it explicitly when it's already known (a rollback just copies
        its target's digest). Otherwise, given `session`+`environment`, it is looked up —
        the ECR repository is tag-MUTABLE, so a tag alone is not a stable pointer to what
        was actually deployed."""
        from api.models.deployment import Deployment
        from api.services.deployment_snapshot import snapshot_env

        try:
            if image_digest is None and session is not None and environment is not None:
                ecr = ECRClient(session)
                repo_name = ECRClient.repository_name_from_url(environment.ecr_repository_url)
                image_digest = ecr.get_image_digest(repo_name, image_tag)

            keys, values_hash = snapshot_env(application.id, application.envs or {})
            Deployment.objects.create(
                application=application,
                image_tag=image_tag,
                image_digest=image_digest,
                commit_sha=resolved_sha,
                tag_source=tag_source or (Deployment.TAG_SOURCE_RESOLVED_SHA if resolved_sha else Deployment.TAG_SOURCE_LATEST),
                compute_type=application.infrastructure.compute_type,
                env_keys=keys,
                env_values_hash=values_hash,
                attached_database_ids=application.attached_database_ids or [],
                cpu=application.alloted_cpu,
                memory=application.alloted_memory,
                port=application.port,
                status=status,
                triggered_by=triggered_by,
                rolled_back_from=rolled_back_from,
            )
        except Exception:
            logger.exception(
                f"Failed to record deployment history for application {application.name} "
                "— the deploy itself is unaffected"
            )

    def _validate_infrastructure(self, application: Application):
        environment = Environment.objects.filter(
            infrastructure=application.infrastructure
        ).first()
        
        if not environment:
            raise ValueError("Infrastructure environment not found")
        
        if environment.status != 'ACTIVE':
            raise ValueError(f"Infrastructure is not active. Current status: {environment.status}")
        
        required = EKS_REQUIRED_ENVIRONMENT_FIELDS if _is_eks(application) else ECS_REQUIRED_ENVIRONMENT_FIELDS
        missing_fields = [field for field in required if not getattr(environment, field)]
        if missing_fields:
            raise ValueError(f"Environment is missing required fields: {', '.join(missing_fields)}")
        
        return environment

    def _abort_if_exited(self, application: Application) -> None:
        """H2 security review R1: a deploy/rollback can run for minutes (CodeBuild, ECS
        service stabilization, ALB target-health polling) — long enough for a customer to
        complete exit after this call started. `application.infrastructure` was read once
        at the top of `deploy_application`/`rollback_application` and is never
        re-fetched, so a check against that in-memory object would miss an exit that
        landed mid-deploy. This does a fresh, minimal read of just the field, and is
        called immediately before every remaining AWS/Kubernetes mutation (ECS
        create/update_service, the EKS apply, ALB listener-rule and host-forward-rule
        creation) so a deploy that was already this far along still stops rather than
        finishing a customer's own record of it having exited.

        Raises `InfrastructureExitedError`, which the caller's existing except block
        already turns into the same cleanup + FAILED status + sanitized error_message
        path a build/AWS failure gets — this does not need its own failure handling."""
        from api.models.infrastructure import Infrastructure
        from api.services.exit_enforcement import InfrastructureExitedError

        exited_at = Infrastructure.objects.filter(
            id=application.infrastructure_id
        ).values_list('exited_at', flat=True).first()
        if exited_at is not None:
            raise InfrastructureExitedError(
                "Infrastructure exited during deployment — aborting before applying further changes."
            )

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
    
    def _trigger_build(self, session, application: Application, environment: Environment, image_tag: str):
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
                Description="Service role for CodeBuild",
                # infra-level only: this role builds every app on the infra, so an
                # app-level tag would misattribute other apps' build cost to whichever
                # app happened to trigger the role's creation.
                Tags=as_key_value_tags(infra_tags(application.infrastructure_id)),
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
        
        codebuild.ensure_project_exists(
            project_name, service_role_arn, session.region_name,
            tags=infra_tags(application.infrastructure_id),
        )
        
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
            image_tag=image_tag,
        )
        
        logger.info(f"Started CodeBuild job {build_id} for application {application.name}")
        return build_id
    
    def _wait_for_build(self, session, build_id):
        codebuild = CodeBuildClient(session)
        logger.info(f"Waiting for build {build_id} to complete")
        resolved_sha = codebuild.wait_for_build(build_id)
        logger.info(f"Build {build_id} completed successfully (commit {resolved_sha or 'unknown'})")
        return resolved_sha
    
    def _database_env_prefix(self, db_name: str) -> str:
        return re.sub(r'[^A-Z0-9]', '_', db_name.upper())

    def _build_database_injections(self, application: Application) -> tuple[dict, list]:
        """Build the plain-env and ECS `secrets` entries for every attached database.
        Injected names always win over `application.envs` on collision — the caller
        strips them from `envs` before merging."""
        from api.models.database import Database

        attached_ids = application.attached_database_ids or []
        if not attached_ids:
            return {}, []

        plain_env = {}
        secrets = []
        for db in Database.objects.filter(id__in=attached_ids, status='ACTIVE'):
            prefix = self._database_env_prefix(db.name)
            plain_env[f"{prefix}_HOST"] = db.host or ""
            plain_env[f"{prefix}_PORT"] = str(db.port or "")

            if not db.secret_arn:
                continue

            if db.engine == "redis":
                plain_env[f"{prefix}_TLS"] = "true"
                secrets.append({"name": f"{prefix}_AUTH_TOKEN", "valueFrom": f"{db.secret_arn}:auth_token::"})
            else:
                # The rds module creates the database as replace(db_name, "-", "_") — RDS
                # DBName allows no hyphens. Injecting the raw Launchpad name handed every
                # hyphenated database's apps a name that doesn't exist on the instance.
                plain_env[f"{prefix}_DB"] = db.name.replace("-", "_")
                secrets.append({"name": f"{prefix}_USERNAME", "valueFrom": f"{db.secret_arn}:username::"})
                secrets.append({"name": f"{prefix}_PASSWORD", "valueFrom": f"{db.secret_arn}:password::"})

        return plain_env, secrets

    def _create_task_definition(self, session, application: Application, environment: Environment,
                                resolved_sha: str | None = None, image_tag: str | None = None,
                                image_digest: str | None = None, host_mode: bool = False,
                                app_hostname: str | None = None):
        ecs = ECSClient(session)
        ecr = ECRClient(session)
        logs = session.client('logs')

        # H4: an app that already has a stored log group keeps it — never move a
        # running app's logs to a new group. An app that has completed at least one
        # deploy before deployed under the legacy, slug-only name before this field
        # existed; recompute that same legacy name rather than minting a new one out
        # from under it. Only a genuinely new app (never completed a deploy) computes
        # the new per-infra+per-app hashed name. Either way the result is persisted
        # once, so every later deploy/rollback/backfill of this row — and every reader
        # (runtime logs, cleanup, exit inventory) via `ecs_log_group_for` — reads the
        # stored value back instead of re-deriving it; this never renames a live
        # resource, since the legacy branch persists the exact string it already used.
        #
        # "Has completed a deploy before" is `_has_succeeded_before` — a stored
        # task_definition_arn/service_arn, or (since ApplicationRetryDeployView resets
        # both of those on a live row without deleting it) Deployment history. Computed
        # at most once (memoized here) since both blocks below may need it and it can
        # run a Deployment query — an app with both names already stored never calls it
        # at all.
        succeeded_before_cache: list[bool] = []

        def succeeded_before() -> bool:
            if not succeeded_before_cache:
                succeeded_before_cache.append(self._has_succeeded_before(application))
            return succeeded_before_cache[0]

        if application.log_group_name:
            log_group_name = application.log_group_name
        else:
            log_group_name = (
                _legacy_ecs_log_group(_slug(application.name)) if succeeded_before()
                else _new_ecs_log_group(application)
            )
            application.log_group_name = log_group_name
            application.save(update_fields=['log_group_name'])

        # H7 residual from H4: identical pattern to log_group_name above — an app that
        # already has a stored task family keeps it, an app that has deployed before
        # (but predates this field) recomputes the same legacy `{slug}-task` name it
        # already used, and only a genuinely new app mints the new hashed family. The
        # family also names the app container inside the task definition, so
        # `_create_ecs_service`'s `container_name` reads this same stored value back.
        if application.task_family:
            task_family = application.task_family
        else:
            task_family = (
                _legacy_ecs_task_family(_slug(application.name)) if succeeded_before()
                else _new_ecs_task_family(application)
            )
            application.task_family = task_family
            application.save(update_fields=['task_family'])

        try:
            logs.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created log group {log_group_name}")
        except logs.exceptions.ResourceAlreadyExistsException:
            logger.info(f"Log group {log_group_name} already exists")

        # Pin the task definition to the immutable per-commit tag. `-latest` moves on every
        # rebuild, so a task definition referencing it does not describe a fixed image and
        # cannot be rolled back to. Falling back to `-latest` keeps deploys working against
        # a CodeBuild project that predates the two-tag buildspec. A rollback passes the
        # exact tag from its target Deployment row instead of recomputing one — the app may
        # have been renamed since, and slug-from-current-name would no longer resolve to the
        # image that was actually pushed. When a digest is also known, `get_image_ref` pins
        # to it instead — the tag alone is not a stable pointer, since the ECR repository is
        # tag-MUTABLE and a later build of the same commit could have repointed it.
        image_tag = image_tag or self._pinned_image_tag(application, resolved_sha)
        image_uri = ecr.get_image_ref(environment.ecr_repository_url, image_tag, image_digest)
        logger.info(f"Task definition for {application.name} pinned to {image_uri}")

        # Now that every build pushes a second, per-commit tag, the repository grows
        # without bound in the customer's account unless retention is set.
        ecr.ensure_lifecycle_policy(ECRClient.repository_name_from_url(environment.ecr_repository_url))

        db_env, db_secrets = self._build_database_injections(application)
        # Injected names win: strip any application.envs key a database injection
        # would otherwise collide with, so the connection info a customer sees is
        # never silently shadowed by their own env var of the same name.
        base_envs = {k: v for k, v in (application.envs or {}).items() if k not in db_env}
        envs = {**base_envs, **db_env, 'PORT': str(application.port)}

        task_def_arn = ecs.create_task_definition(
            family=task_family,
            image=image_uri,
            cpu=application.alloted_cpu,
            memory=application.alloted_memory,
            envs=envs,
            execution_role_arn=environment.ecs_task_execution_role_arn,
            container_port=application.port,
            app_name=_slug(application.name),
            secrets=db_secrets,
            tags=app_tags(application.infrastructure_id, _slug(application.name)),
            host_mode=host_mode, app_hostname=app_hostname,
            log_group=log_group_name,
        )

        logger.info(f"Created task definition {task_def_arn}")
        return task_def_arn
    
    def _create_target_group(self, session, application: Application, environment: Environment, health_check_path: str = '/'):
        alb = ALBClient(session)

        if application.target_group_arn:
            try:
                resp = alb.client.describe_target_groups(TargetGroupArns=[application.target_group_arn])
                tg = resp['TargetGroups'][0]
                if tg['VpcId'] == environment.vpc_id:
                    logger.info(f"Reusing existing target group {application.target_group_arn}")
                    # A routing-mode change (path <-> host) on a redeploy must move the
                    # health check in lockstep with the nginx sidecar this deploy is about
                    # to ship, whether or not the target group itself is new.
                    alb.modify_target_group(application.target_group_arn, health_check_path)
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

        # H4: named from a hash of both the infrastructure id and the application id
        # (api/common/naming.py:target_group_name), never a UUID prefix — a truncated
        # infra id repeats every ~65s platform-wide, and two infras deployed within
        # that window with the same app slug would land on the exact same name, which
        # CreateTargetGroup treats as "reuse this one" (see ALBClient.create_target_group's
        # DuplicateTargetGroupNameException handling below) rather than an error.
        tg_name = _target_group_name(application)
        target_group_arn = alb.create_target_group(
            name=tg_name, vpc_id=environment.vpc_id, port=80,
            # H4 C1: launchpad:app-id (this row's own id) in addition to app_tags()'s
            # name-shaped keys — the ownership check's expected tags, so it doesn't
            # rely solely on the name hash to distinguish a recreated app from its
            # predecessor.
            tags=_target_group_tags(application.infrastructure_id, _slug(application.name), application.id),
            health_check_path=health_check_path,
        )
        logger.info(f"Created target group {target_group_arn}")
        return target_group_arn

    def _target_group_is_attached(self, alb: ALBClient, target_group_arn: str) -> bool:
        """Real AWS: ECS CreateService raises InvalidParameterException for a target
        group that has no associated load balancer yet, and an unattached target group
        is never health-checked either way — see _create_ecs_service_with_routing for
        why this decides the create-vs-route ordering."""
        response = alb.client.describe_target_groups(TargetGroupArns=[target_group_arn])
        return bool(response['TargetGroups'][0].get('LoadBalancerArns'))

    def _create_ecs_service_with_routing(self, session, application: Application, environment: Environment,
                                         host_mode: bool, app_hostname: str | None, created_resources: list) -> str:
        """Creates the ECS service and wires up its ALB routing, choosing the order
        based on whether `application.target_group_arn` is already attached to the ALB.

        #43 moved routing to after the service is confirmed healthy — no traffic until
        healthy, zero 502 window. On real AWS this breaks a brand-new target group: ECS
        CreateService raises InvalidParameterException for a target group with no
        associated load balancer, and an unattached target group is never health-checked
        anyway — every first deploy failed this way, while redeploys worked only because
        their target group was already attached from a prior deploy. A new target group
        has no existing traffic to protect, so attaching it to the ALB before the service
        exists is safe; an already-attached one keeps #43's original healthy-first order.

        Used by both `deploy_application` and `_recreate_ecs_service` so the ordering
        decision lives in one place. Returns the :80 listener ARN, for the caller's
        subsequent `verify_target_group_attached` call.
        """
        alb = ALBClient(session)
        route_before_create = not self._target_group_is_attached(alb, application.target_group_arn)

        def create_and_wait_healthy():
            service_arn = self._create_ecs_service(session, application, environment)
            application.service_arn = service_arn
            application.desired_count = 1
            # Narrow update_fields: a webhook or other concurrent write to this row
            # (e.g. project_commit_hash advancing while this deploy is in flight) must
            # not be clobbered by a full save from this worker's stale in-memory copy.
            application.save(update_fields=['service_arn', 'desired_count'])
            created_resources.append(('ecs_service', service_arn))

            service_name = f"{_slug(application.name)}-service"
            self._wait_for_service_stable_with_refresh(
                application.infrastructure, environment.cluster_arn, service_name,
                expected_task_definition_arn=application.task_definition_arn,
            )
            logger.info(f"Service {service_name} is stable and running")

            self._wait_for_target_healthy(alb, application.target_group_arn, desired_count=application.desired_count)
            logger.info(f"Target group {application.target_group_arn} has healthy targets")

        def route():
            # H2: a build can run for minutes before either branch reaches this point —
            # re-check for exit before the first AWS mutation this order performs.
            self._abort_if_exited(application)

            # R1: reserve the infra-wide :80 wildcard redirect's priority-1 slot before
            # this (or any) app's own path rule is created — see
            # _reserve_host_redirect_priority's docstring for why this runs independent
            # of whether THIS deploy is itself eligible for host mode.
            self._reserve_host_redirect_priority(alb, application, environment)

            listener_rule_arn, listener_arn = self._configure_alb_routing(session, application, environment)
            application.listener_rule_arn = listener_rule_arn
            application.save(update_fields=['listener_rule_arn'])
            created_resources.append(('listener_rule', listener_rule_arn))

            # Host-mode routing (443 forward) — additive, on top of the path rule above,
            # never in place of it.
            host_forward_rule_arn = self._configure_host_routing(alb, application, environment, host_mode, app_hostname)
            if host_forward_rule_arn:
                created_resources.append(('host_forward_rule', host_forward_rule_arn))

            return listener_arn

        if route_before_create:
            listener_arn = route()
            create_and_wait_healthy()
        else:
            create_and_wait_healthy()
            listener_arn = route()

        return listener_arn

    def _get_app_sg_name(self, application: Application) -> str:
        return app_security_group_name(application.infrastructure_id)

    def _get_or_create_app_security_group(self, ec2, application: Application, environment: Environment, alb_sg_id: str) -> str:
        sg_id = _shared_get_or_create_app_sg(ec2, application.infrastructure_id, environment.vpc_id)

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
                # Falling back to ALL subnets while assignPublicIp stays DISABLED means a task
                # landing in a public subnet (no NAT) can't reach ECR and the deploy fails to
                # pull its image. This is almost always a VPC-tagging gap — surface it loudly.
                logger.error(
                    "No subnets tagged Type=private in VPC %s — falling back to all subnets with "
                    "assignPublicIp=DISABLED. If any are public/NAT-less, ECR image pull will fail. "
                    "Tag private subnets Type=private and ensure a NAT path.",
                    environment.vpc_id,
                )
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
        
        self._abort_if_exited(application)
        service_arn = ecs.create_service(
            cluster_arn=environment.cluster_arn,
            service_name=f"{_slug(application.name)}-service",
            task_definition_arn=application.task_definition_arn,
            target_group_arn=application.target_group_arn,
            subnet_ids=subnet_ids,
            security_group_ids=security_group_ids,
            container_name=_ecs_task_family_for(application),
            container_port=application.port,
            use_nginx=True,
            tags=app_tags(application.infrastructure_id, _slug(application.name)),
        )
        
        logger.info(f"Created ECS service {service_arn}")
        return service_arn
    
    def _configure_alb_routing(self, session, application: Application, environment: Environment):
        alb = ALBClient(session)
        
        listener_arn = alb.get_listener_arn(environment.alb_arn)
        if not listener_arn:
            raise ValueError(f"No :80 listener found on ALB {environment.alb_arn}")

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
        slug = _slug(application.name)

        self._abort_if_exited(application)
        listener_rule_arn = alb.create_listener_rule(
            listener_arn=listener_arn,
            target_group_arn=application.target_group_arn,
            # R1: an exact match plus a trailing-slash prefix, never a bare glob — `/a*`
            # (the old shape) also matches `/ab/x`, so once ensure_host_redirect_rule's
            # priority-1 swap can reorder path rules relative to each other, two apps
            # whose slugs share a prefix ("a" and "ab") could start shadowing one
            # another depending on which one happens to end up at a lower priority
            # number after a swap. Exact-or-prefixed-with-slash patterns never overlap
            # between different slugs regardless of priority order.
            path_pattern=[f"/{slug}", f"/{slug}/*"],
            priority=priority,
            tags=app_tags(application.infrastructure_id, slug),
        )

        logger.info(f"Created listener rule {listener_rule_arn}")
        return listener_rule_arn, listener_arn

    def _reserve_host_redirect_priority(self, alb: ALBClient, application: Application, environment: Environment):
        """R1: claim the infra-wide :80 wildcard redirect's priority-1 slot before this (or
        any) app's own path rule exists, independent of whether THIS deploy is itself
        eligible for host mode — see the call site in deploy_application for why. A no-op
        when the platform domain isn't configured or the infra has no dns_label yet (there
        is nothing to build a wildcard condition from); never fails the deploy."""
        infra = application.infrastructure
        base_domain = self._base_domain()
        if not infra.dns_label or not base_domain:
            return
        try:
            http_listener_arn = alb.get_listener_arn(environment.alb_arn, port=80)
            if http_listener_arn:
                alb.ensure_host_redirect_rule(http_listener_arn, infra.dns_label, base_domain)
        except Exception:
            logger.warning(
                "Could not reserve host-redirect priority for infra %s (non-fatal)",
                infra.id, exc_info=True,
            )

    def _resolve_host_routing(self, application: Application, environment: Environment, session) -> tuple:
        """F1b part 3a. Returns (host_mode, app_hostname, reason) — reason is None when
        host_mode is True, otherwise a stable string (see api/common/host_url.py and
        views/application.py's host_url_status) explaining why this deploy ran in path
        mode instead. Never raises: an app ineligible for host mode this time simply
        deploys in path mode, exactly as every app did before this feature existed.

        The infra-level DB fields (tls_status/dns_synced/https_ready, mirrored from
        infrastructure-service — see api/common/host_url.py:infra_host_ready) are a fast
        pre-check; the live :443 describe_listeners call right after is authoritative,
        since those mirrored fields can be briefly stale relative to the real ALB.
        """
        infra = application.infrastructure
        ready, reason = infra_host_ready(infra)
        if not ready:
            return False, None, reason

        try:
            hostname = build_app_hostname(infra.dns_label, _slug(application.name))
        except HostUrlNotAvailable as exc:
            return False, None, exc.reason

        alb = ALBClient(session)
        https_listener_arn = alb.get_listener_arn(environment.alb_arn, port=443)
        if not https_listener_arn:
            logger.warning(
                "infra %s reports https_ready but no :443 listener exists on ALB %s yet — "
                "deploying %s in path mode this time",
                infra.id, environment.alb_arn, application.name,
            )
            return False, None, "https_listener_not_applied"

        return True, hostname, None

    def _configure_host_routing(self, alb: ALBClient, application: Application,
                                environment: Environment, host_mode: bool, app_hostname: str | None):
        """Additive on top of _configure_alb_routing's path rule — never removes it. A
        deploy that downgrades out of host mode (TLS/DNS regressed since the last deploy,
        vanishingly rare) tears down its own stale 443 forward rule rather than leaving a
        rule that would forward a request straight into an app whose nginx no longer
        expects to be reached by that Host."""
        self._abort_if_exited(application)
        if not host_mode:
            if application.host_forward_rule_arn:
                self._delete_host_forward_rule(alb, application)
            return None

        infra = application.infrastructure
        https_listener_arn = alb.get_listener_arn(environment.alb_arn, port=443)
        http_listener_arn = alb.get_listener_arn(environment.alb_arn, port=80)
        # _resolve_host_routing confirmed the :443 listener existed moments ago, but the
        # build+wait steps between then and here can take minutes — long enough for a
        # concurrent re-provision to have torn it down. Fail loudly rather than handing
        # boto3 a None ARN (a confusing ParamValidationError) or silently no-op-ing with
        # nginx already baked for host mode.
        if not https_listener_arn or not http_listener_arn:
            raise ValueError(
                f"ALB {environment.alb_arn} is missing its :443 or :80 listener — cannot "
                f"configure host-mode routing for {application.name}"
            )

        alb.ensure_host_redirect_rule(http_listener_arn, infra.dns_label, self._base_domain())

        if application.host_forward_rule_arn:
            self._delete_host_forward_rule(alb, application)

        host_forward_rule_arn = alb.create_host_forward_rule(
            https_listener_arn, application.target_group_arn, app_hostname,
            tags=app_tags(application.infrastructure_id, _slug(application.name)),
        )
        application.host_forward_rule_arn = host_forward_rule_arn
        application.save(update_fields=['host_forward_rule_arn'])
        logger.info(f"Configured host-mode routing for {application.name} at https://{app_hostname}")
        return host_forward_rule_arn

    def _delete_host_forward_rule(self, alb: ALBClient, application: Application):
        try:
            alb.client.delete_rule(RuleArn=application.host_forward_rule_arn)
        except ClientError as e:
            if e.response['Error']['Code'] != 'RuleNotFound':
                logger.error(f"Could not delete old host-forward rule {application.host_forward_rule_arn}: {e}")
                raise
        application.host_forward_rule_arn = None
        application.save(update_fields=['host_forward_rule_arn'])

    def _base_domain(self) -> str:
        from django.conf import settings
        return settings.PLATFORM_BASE_DOMAIN

    def _generate_deployment_url(self, application: Application, environment: Environment):
        return f"http://{environment.alb_dns}/{_slug(application.name)}"
    
    def _wait_for_target_healthy(self, alb: ALBClient, target_group_arn: str, desired_count: int = 1, timeout: int = 180):
        """Poll ALB target health until at least desired_count targets are healthy."""
        import time as _time
        deadline = _time.time() + timeout
        interval = 10
        while _time.time() < deadline:
            resp = alb.client.describe_target_health(TargetGroupArn=target_group_arn)
            healthy = sum(1 for t in resp['TargetHealthDescriptions'] if t['TargetHealth']['State'] == 'healthy')
            logger.info(f"Target group {target_group_arn}: {healthy}/{desired_count} healthy targets")
            if healthy >= desired_count:
                return
            _time.sleep(interval)
        raise Exception(f"Target group {target_group_arn} had no healthy targets after {timeout}s — not routing traffic")

    def _wait_for_service_stable_with_refresh(
        self, infrastructure, cluster_arn, service_name, timeout=300, expected_task_definition_arn=None,
    ):
        """Wait for ECS service stability, refreshing credentials on token expiry.

        `expected_task_definition_arn` is the task definition THIS call's caller just told
        ECS to run — every caller passes its own, so a deploymentCircuitBreaker auto-
        rollback (ECS silently converging the service back onto the previous task
        definition) is caught here instead of read as a stable deploy of the new one."""
        logger.info(f"Waiting for service {service_name} to become stable...")
        session = self._create_aws_session(infrastructure)
        ecs = ECSClient(session)
        start_time = time.time()

        while True:
            try:
                ecs.wait_for_service_stable(
                    cluster_arn, service_name, timeout=int(timeout - (time.time() - start_time)),
                    expected_task_definition_arn=expected_task_definition_arn,
                )
                return
            except Exception as e:
                if 'ExpiredToken' in str(e):
                    logger.warning("Token expired during wait, refreshing credentials")
                    session = self._create_aws_session(infrastructure)
                    ecs = ECSClient(session)
                else:
                    raise

    # ── Rollback ─────────────────────────────────────────────────────────────
    #
    # Skips CodeBuild entirely: pin to an image already in ECR and restore the CPU/memory
    # /port/image that shipped with it. Env values are always re-read from the application's
    # current envs, never from the snapshot — rolling back code must not roll back a
    # rotated credential. No Application field is written until every AWS call below has
    # already succeeded, so a failure partway through never leaves the row half restored.

    def rollback_application(self, application: Application, target) -> str:
        created_resources = []
        session = None
        environment = None
        try:
            environment = self._validate_infrastructure(application)
            session = self._create_aws_session(application.infrastructure)

            ecr = ECRClient(session)
            repo_name = ECRClient.repository_name_from_url(environment.ecr_repository_url)
            if not ecr.image_exists(repo_name, target.image_tag, digest=target.image_digest):
                raise ValueError(
                    f"Image '{target.image_tag}' is no longer available in the container "
                    "registry — it was likely expired by the ECR retention policy. Choose "
                    "a more recent deployment to roll back to."
                )

            if _is_eks(application):
                deployment_url = self._rollback_eks(session, application, environment, target, created_resources)
            else:
                deployment_url = self._rollback_ecs(session, application, environment, target, created_resources)

        except Exception as e:
            logger.exception(f"Rollback failed for application {application.name}")
            if session and created_resources:
                for resource_type, resource_id in reversed(created_resources):
                    try:
                        self._cleanup_resource(session, resource_type, resource_id, application, environment)
                    except Exception as cleanup_error:
                        logger.error(f"Failed to cleanup {resource_type} {resource_id} during rollback unwind: {cleanup_error}")
            application.status = 'FAILED'
            application.error_message = sanitize_deploy_error(e)
            application.save(update_fields=['status', 'error_message'])
            self._record_deployment(
                application, image_tag=target.image_tag, resolved_sha=target.commit_sha,
                status='FAILED', triggered_by='ROLLBACK', tag_source=target.tag_source,
                rolled_back_from=target, image_digest=target.image_digest,
            )
            raise

        self._record_deployment(
            application, image_tag=target.image_tag, resolved_sha=target.commit_sha,
            status='SUCCEEDED', triggered_by='ROLLBACK', tag_source=target.tag_source,
            rolled_back_from=target, image_digest=target.image_digest,
        )
        logger.info(f"Application {application.name} rolled back to deployment {target.id}")
        return deployment_url

    def _rollback_ecs(self, session, application: Application, environment: Environment, target,
                      created_resources: list) -> str:
        ecs = ECSClient(session)

        # Routing mode is re-resolved at rollback time too, not carried over from
        # whatever the target Deployment snapshot's era looked like — TLS/DNS readiness
        # (or the lack of it) may have changed since, and the health check + nginx
        # sidecar must always match what's true right now.
        host_mode, app_hostname, _host_reason = self._resolve_host_routing(application, environment, session)

        # Build the new task definition against the SNAPSHOT's cpu/memory/port, not the
        # application's current ones — a rollback restores the resource shape that shipped
        # with the image, not whatever is configured today. Nothing is persisted yet: if
        # anything below raises, `application` still reflects its pre-rollback state.
        original = (application.alloted_cpu, application.alloted_memory, application.port)
        application.alloted_cpu, application.alloted_memory, application.port = target.cpu, target.memory, target.port
        recreate = False
        try:
            task_def_arn = self._create_task_definition(
                session, application, environment,
                image_tag=target.image_tag, image_digest=target.image_digest,
                host_mode=host_mode, app_hostname=app_hostname,
            )
            service_name = f"{_slug(application.name)}-service"
            self._abort_if_exited(application)
            try:
                ecs.client.update_service(
                    cluster=environment.cluster_arn,
                    service=service_name,
                    taskDefinition=task_def_arn,
                    forceNewDeployment=True,
                )
            except (ecs.client.exceptions.ServiceNotFoundException,
                    ecs.client.exceptions.ServiceNotActiveException):
                # The service this rollback meant to re-pin no longer exists — most often
                # ApplicationRetryDeployView's cleanup job tore it down (along with the
                # target group and listener rule) after a prior failed deploy, or someone
                # removed it by hand. ApplicationCleanupService._delete_ecs_service waits
                # for the service to reach INACTIVE rather than for its record to vanish
                # outright, so UpdateService can raise either exception depending on
                # exactly when the rollback lands relative to that cleanup — both mean
                # the same thing here. Recreate everything a normal deploy would, pinned
                # to the already-registered rollback task definition, instead of dead-
                # lettering on the same error three retries in a row.
                logger.warning(
                    "Rollback target service %s not found/active for %s — recreating it",
                    service_name, application.name,
                )
                recreate = True
        except Exception:
            application.alloted_cpu, application.alloted_memory, application.port = original
            raise

        # Everything above succeeded — commit the restored config in one write.
        application.task_definition_arn = task_def_arn
        application.status = 'DEPLOYING'
        application.error_message = None
        application.save(update_fields=[
            'alloted_cpu', 'alloted_memory', 'port', 'task_definition_arn', 'status', 'error_message',
        ])

        alb = ALBClient(session)
        if recreate:
            self._recreate_ecs_service(
                session, application, environment, host_mode, app_hostname, created_resources,
            )
        else:
            self._wait_for_service_stable_with_refresh(
                application.infrastructure, environment.cluster_arn, service_name,
                expected_task_definition_arn=application.task_definition_arn,
            )
            self._wait_for_target_healthy(alb, application.target_group_arn, desired_count=application.desired_count)

            health_check_path = HOST_MODE_HEALTH_CHECK_PATH if host_mode else '/'
            alb.modify_target_group(application.target_group_arn, health_check_path)
            self._configure_host_routing(alb, application, environment, host_mode, app_hostname)

        deployment_url = self._generate_deployment_url(application, environment)
        application.deployment_url = deployment_url
        application.status = 'ACTIVE'
        application.save(update_fields=['deployment_url', 'status'])
        return deployment_url

    def _recreate_ecs_service(self, session, application: Application, environment: Environment,
                              host_mode: bool, app_hostname: str | None, created_resources: list):
        """F3 gap: the ECS service `_rollback_ecs` meant to update is gone (see its
        ServiceNotFoundException/ServiceNotActiveException handling above). Rebuilds
        everything a normal deploy creates after its task definition — target group,
        service, ALB routing — reusing the exact steps `deploy_application` runs for ECS
        rather than duplicating them. `application.task_definition_arn` is already pinned
        to the rollback target's image by the caller; `_create_target_group`/
        `_create_ecs_service` fall back to creating fresh resources when the
        application's stored ARNs are also gone (the usual case when a retry's cleanup is
        what deleted the service).

        Appends to `created_resources` exactly as `deploy_application` does, so
        `rollback_application`'s failure handler tears these back down — rather than
        leaving them running in the customer's account — if a later step (service never
        stabilizes, ALB never reports healthy) fails."""
        health_check_path = HOST_MODE_HEALTH_CHECK_PATH if host_mode else '/'
        target_group_arn = self._create_target_group(
            session, application, environment, health_check_path=health_check_path,
        )
        application.target_group_arn = target_group_arn
        application.save(update_fields=['target_group_arn'])
        created_resources.append(('target_group', target_group_arn))

        listener_arn = self._create_ecs_service_with_routing(
            session, application, environment, host_mode, app_hostname, created_resources,
        )

        alb = ALBClient(session)
        alb.verify_target_group_attached(application.target_group_arn, listener_arn)

    def _rollback_eks(self, session, application: Application, environment: Environment, target, created_resources: list) -> str:
        ecr = ECRClient(session)
        image_uri = ecr.get_image_ref(environment.ecr_repository_url, target.image_tag, target.image_digest)

        # EKSDeployer reads cpu/memory/port/envs straight off `application`, so the snapshot
        # values have to be in place before it builds the manifest. `deploy()` patches the
        # existing Deployment/Service/Ingress in one call each (409 on create -> patch), so
        # this is "patch image + env" — nothing pre-existing is torn down first.
        original = (application.alloted_cpu, application.alloted_memory, application.port)
        application.alloted_cpu, application.alloted_memory, application.port = target.cpu, target.memory, target.port
        try:
            self._abort_if_exited(application)
            EKSDeployer(session, application, environment).deploy(image_uri, created_resources)
        except Exception:
            application.alloted_cpu, application.alloted_memory, application.port = original
            raise

        deployment_url = self._generate_deployment_url(application, environment)
        application.deployment_url = deployment_url
        application.status = 'ACTIVE'
        application.error_message = None
        application.save(update_fields=[
            'alloted_cpu', 'alloted_memory', 'port', 'deployment_url', 'status', 'error_message',
        ])
        return deployment_url

    # ── Backfill (F1b part 3a) ───────────────────────────────────────────────
    #
    # Moves an already-ACTIVE ECS app into host-mode routing without waiting for its next
    # code push, once its infrastructure becomes TLS-ready after the app was last deployed.
    # No CodeBuild: re-registers the task definition against the exact image (tag+digest)
    # its most recent successful Deployment row recorded — the same "pin to a known-good
    # image, no rebuild" approach rollback uses — with the host-mode nginx config baked in.
    # Idempotent: a no-op for an app that isn't eligible, or is already in host mode.
    # EKS needs no equivalent command — EKSDeployer re-resolves host_mode on every deploy,
    # so an EKS app picks up host mode automatically the next time it is deployed for any
    # reason; there is no "wait for a push" gap to backfill there.

    def evaluate_backfill_eligibility(self, application: Application) -> tuple:
        """Read-only: every check `backfill_host_routing` needs before it mutates anything,
        split out so `--dry-run` can report exactly what a real run would decide without
        ever calling ECS/ALB. The live `_resolve_host_routing` call this makes is itself
        read-only (`describe_listeners`), never a mutation.

        Returns (eligible, reason, context). `context` is only populated when eligible —
        the session/environment/target/hostname `backfill_host_routing` then acts on,
        so eligibility is computed exactly once per call rather than re-derived."""
        from shared.enums.orchestrator import ComputeType

        if application.infrastructure.exited_at is not None:
            return False, "infrastructure_exited", None
        if _is_eks(application):
            return False, "eks_not_applicable", None
        if application.status != 'ACTIVE':
            return False, f"status_{application.status.lower()}", None
        if application.host_forward_rule_arn:
            return False, "already_host_mode", None
        if application.infrastructure.compute_type != ComputeType.ECS_FARGATE:
            return False, "not_ecs", None

        environment = Environment.objects.filter(infrastructure=application.infrastructure).first()
        if environment is None or environment.status != 'ACTIVE':
            return False, "environment_not_active", None

        target = self._latest_succeeded_deployment(application)
        if target is None:
            return False, "no_deployment_history", None

        session = self._create_aws_session(application.infrastructure)
        host_mode, app_hostname, reason = self._resolve_host_routing(application, environment, session)
        if not host_mode:
            return False, reason, None

        return True, "eligible", {
            "session": session, "environment": environment,
            "target": target, "app_hostname": app_hostname,
        }

    def backfill_host_routing(self, application: Application) -> tuple[bool, str]:
        """Returns (migrated, reason). `reason` is a short human string either way — a
        skip reason (e.g. 'tls_not_issued', 'already_host_mode'), 'failed_rolled_back' (the
        ECS service was moved to the new task definition but something after that failed —
        see _rollback_backfill), or 'migrated'.

        Caller's responsibility, not this method's (matching how deploy_application/
        rollback_application work — the worker loop holds the lock around them, not the
        service methods themselves): take `DeploymentLock` for `application.id` before
        calling this, so a concurrent ordinary deploy/rollback of the same app can never
        race the ECS service update below."""
        eligible, reason, ctx = self.evaluate_backfill_eligibility(application)
        if not eligible:
            return False, reason

        session, environment = ctx["session"], ctx["environment"]
        target, app_hostname = ctx["target"], ctx["app_hostname"]

        original_task_definition_arn = application.task_definition_arn
        task_def_arn = self._create_task_definition(
            session, application, environment,
            image_tag=target.image_tag, image_digest=target.image_digest,
            host_mode=True, app_hostname=app_hostname,
        )
        ecs = ECSClient(session)
        service_name = f"{_slug(application.name)}-service"
        self._abort_if_exited(application)
        ecs.client.update_service(
            cluster=environment.cluster_arn, service=service_name,
            taskDefinition=task_def_arn, forceNewDeployment=True,
        )
        application.task_definition_arn = task_def_arn
        application.save(update_fields=['task_definition_arn'])

        try:
            self._wait_for_service_stable_with_refresh(
                application.infrastructure, environment.cluster_arn, service_name,
                expected_task_definition_arn=application.task_definition_arn,
            )
            alb = ALBClient(session)
            self._wait_for_target_healthy(alb, application.target_group_arn, desired_count=application.desired_count)
            alb.modify_target_group(application.target_group_arn, HOST_MODE_HEALTH_CHECK_PATH)
            self._configure_host_routing(alb, application, environment, True, app_hostname)
        except Exception:
            logger.exception(
                f"backfill_host_routing: {application.name} failed after the ECS service "
                "was already moved to the new task definition — rolling back"
            )
            self._rollback_backfill(session, application, environment, service_name, original_task_definition_arn)
            return False, "failed_rolled_back"

        logger.info(f"backfill_host_routing: migrated {application.name} to host mode at https://{app_hostname}")
        return True, "migrated"

    def _rollback_backfill(self, session, application: Application, environment: Environment,
                           service_name: str, original_task_definition_arn: str | None):
        """RECOMMENDED item 4 (security review): a failure between the ECS service update
        and a fully-configured host route must not leave the service running the new
        (host-mode) task definition with none of the routing that makes it reachable —
        restore the task definition it ran before this attempt, if there was one."""
        if not original_task_definition_arn:
            logger.warning(
                f"backfill_host_routing: {application.name} has no prior task definition to "
                "roll back to — leaving the new one in place"
            )
            return
        try:
            ecs = ECSClient(session)
            ecs.client.update_service(
                cluster=environment.cluster_arn, service=service_name,
                taskDefinition=original_task_definition_arn, forceNewDeployment=True,
            )
            Application.objects.filter(id=application.id).update(task_definition_arn=original_task_definition_arn)
            application.task_definition_arn = original_task_definition_arn
        except Exception:
            logger.exception(
                f"backfill_host_routing: rollback of {application.name} to "
                f"{original_task_definition_arn} also failed — service left on the new "
                "task definition with incomplete routing"
            )

    def _latest_succeeded_deployment(self, application: Application):
        from api.models.deployment import Deployment
        return Deployment.objects.filter(
            application=application, status=Deployment.STATUS_SUCCEEDED, tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA,
        ).order_by('-created_at').first()

    def _has_succeeded_before(self, application: Application) -> bool:
        """H4: whether this app has ever completed a deploy — the signal
        `_create_task_definition` uses to decide the legacy vs. new-hashed log group
        name.

        `deployment_url` is checked first: it is set only by a fully successful deploy
        (`deploy_application`'s step 9) and `ApplicationRetryDeployView` does not reset
        it (it resets `status`, `error_message`, `service_arn`, `task_definition_arn`,
        `target_group_arn`, `listener_rule_arn`, `runtime_refs` — not this field, and
        not `build_id`), so it survives every reset that field's own siblings don't. A
        stored `task_definition_arn`/`service_arn` is checked next (an app deployed
        before Deployment history existed — migration 0029 added that table with no
        backfill — or whose `_record_deployment` call silently failed, since that write
        is best-effort and never fails the deploy it describes, would otherwise have
        neither of those and no Deployment row either). Falls back to Deployment
        history last, for the case none of the above cover: a retry queued in the
        narrow window between `ApplicationRetryDeployView` resetting the ARN fields and
        a worker picking up the resulting job — `deployment_url` from the app's PRIOR
        successful deploy already closes that for the common case, but a Deployment row
        is the final backstop.

        (`api/migrations/0035_backfill_legacy_log_group_name.py` persists
        `log_group_name` directly for every row this method would already call
        "has succeeded before" as of this PR shipping, so this method only matters
        for a row that somehow still has no stored name after that migration ran.)"""
        if application.deployment_url or application.task_definition_arn or application.service_arn:
            return True
        from api.models.deployment import Deployment
        return Deployment.objects.filter(application=application, status=Deployment.STATUS_SUCCEEDED).exists()
