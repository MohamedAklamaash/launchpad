"""One-off backfill: apply launchpad:infra/launchpad:app tags to per-app AWS resources
created before per-app tagging existed (see aws/tags.py, aws/ecs.py, aws/alb.py,
aws/codebuild.py). Tags are not retroactive — a resource this command doesn't reach stays
untagged until its app is redeployed.

Idempotent: tag_resource/add_tags/tag_role set a tag value rather than append, and
update_service without forceNewDeployment only flips enableECSManagedTags/propagateTags on
the service record — existing tasks pick up tags at their next replacement, not immediately.
Running this command twice, or interrupting it partway, is safe.

Skips mock infrastructures — create_boto3_session() would refuse a real AssumeRole against
one, and a mock infra accrues no real AWS cost to attribute.
"""
import logging
import re

from aws.session import create_boto3_session
from aws.tags import app_tags, as_key_value_tags, as_lower_tags, infra_tags
from django.core.management.base import BaseCommand

from api.common.naming import app_slug
from api.models import Application, Environment, Infrastructure

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill launchpad:infra/launchpad:app tags onto existing per-app AWS resources"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be tagged without calling AWS")

    def handle(self, *args, dry_run, **options):
        stats = {"infras": 0, "skipped_mock": 0, "apps": 0, "resources_tagged": 0, "errors": 0}

        for infra in Infrastructure.objects.all().order_by("id"):
            if infra.is_mock:
                stats["skipped_mock"] += 1
                continue

            apps = list(Application.objects.filter(infrastructure=infra))
            if not apps:
                continue

            stats["infras"] += 1
            session = None
            if not dry_run:
                try:
                    session = create_boto3_session(infra)
                except Exception:
                    logger.exception("Could not create an AWS session for infra %s — skipping", infra.id)
                    stats["errors"] += 1
                    continue

            self._backfill_codebuild(infra, session, dry_run, stats)
            for app in apps:
                stats["apps"] += 1
                self._backfill_app(infra, app, session, dry_run, stats)

        verb = "would tag" if dry_run else "tagged"
        self.stdout.write(
            f"infras processed: {stats['infras']}, skipped (mock): {stats['skipped_mock']}, "
            f"apps seen: {stats['apps']}, resources {verb}: {stats['resources_tagged']}, "
            f"errors: {stats['errors']}"
        )

    def _backfill_codebuild(self, infra, session, dry_run, stats):
        # Named per-infrastructure (matches ApplicationDeploymentService._trigger_build) —
        # one project/role builds every app on the infra, so only launchpad:infra applies.
        project_name = re.sub(r'[^a-zA-Z0-9\-_]', '', f"launchpad-build-{infra.id}")
        role_name = f"launchpad-codebuild-role-{infra.id}"
        tags = infra_tags(infra.id)

        if dry_run:
            self.stdout.write(f"[dry-run] would tag CodeBuild project {project_name} and role {role_name}")
            return

        codebuild = session.client('codebuild')
        try:
            if codebuild.batch_get_projects(names=[project_name]).get('projects'):
                codebuild.update_project(name=project_name, tags=as_lower_tags(tags))
                stats["resources_tagged"] += 1
        except Exception:
            logger.exception("Could not tag CodeBuild project %s", project_name)
            stats["errors"] += 1

        iam = session.client('iam')
        try:
            iam.get_role(RoleName=role_name)
            iam.tag_role(RoleName=role_name, Tags=as_key_value_tags(tags))
            stats["resources_tagged"] += 1
        except iam.exceptions.NoSuchEntityException:
            pass
        except Exception:
            logger.exception("Could not tag CodeBuild role %s", role_name)
            stats["errors"] += 1

    def _backfill_app(self, infra, app, session, dry_run, stats):
        tags = app_tags(infra.id, app_slug(app.name))

        if dry_run:
            self.stdout.write(f"[dry-run] would tag resources for app {app.id} ({app.name})")
            return

        ecs = session.client('ecs')
        elbv2 = session.client('elbv2')

        if app.task_definition_arn:
            self._tag_ecs(ecs, app.task_definition_arn, tags, stats)
        if app.service_arn:
            self._tag_ecs(ecs, app.service_arn, tags, stats)
            self._reassert_service_propagation(ecs, app, stats)
        if app.target_group_arn:
            self._tag_elbv2(elbv2, app.target_group_arn, tags, stats)
        if app.listener_rule_arn:
            self._tag_elbv2(elbv2, app.listener_rule_arn, tags, stats)

    def _tag_ecs(self, ecs, resource_arn, tags, stats):
        try:
            ecs.tag_resource(resourceArn=resource_arn, tags=as_lower_tags(tags))
            stats["resources_tagged"] += 1
        except Exception:
            logger.exception("Could not tag ECS resource %s", resource_arn)
            stats["errors"] += 1

    def _tag_elbv2(self, elbv2, resource_arn, tags, stats):
        try:
            elbv2.add_tags(ResourceArns=[resource_arn], Tags=as_key_value_tags(tags))
            stats["resources_tagged"] += 1
        except Exception:
            logger.exception("Could not tag ELBv2 resource %s", resource_arn)
            stats["errors"] += 1

    def _reassert_service_propagation(self, ecs, app, stats):
        environment = Environment.objects.filter(infrastructure_id=app.infrastructure_id).first()
        if not environment or not environment.cluster_arn:
            return
        try:
            ecs.update_service(
                cluster=environment.cluster_arn,
                service=app.service_arn,
                enableECSManagedTags=True,
                propagateTags='SERVICE',
            )
            stats["resources_tagged"] += 1
        except Exception:
            logger.exception("Could not reassert tag propagation on service %s", app.service_arn)
            stats["errors"] += 1
