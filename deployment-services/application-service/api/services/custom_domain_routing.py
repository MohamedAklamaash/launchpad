"""ALB wiring for VALIDATED custom domains (F1b part 3b) — the application-service half of
the split described in plan/F1b-tls-activation.md's "Part 3b" design section.
infrastructure-service owns `CustomDomain` and the ACM certificate; this module owns the
listener's SNI certificate attachment and the app's host-forward/redirect rules, reached
over a synchronous internal HTTP call (api/views/custom_domains_internal.py) rather than a
RabbitMQ event, so infrastructure-service's PENDING->VALIDATED transition can attempt the
attach, and roll back to PENDING, inside one DB transaction on the Infrastructure row lock.

ECS only: EKS custom domains stay disabled while EKS_HOST_MODE_ENABLED is off (matching
platform hostnames — see api/k8s/deployer.py) and are refused by infrastructure-service at
claim time, so this module never needs an EKS branch.
"""
import logging

from aws.tags import app_tags
from django.conf import settings

from api.common.naming import app_slug

logger = logging.getLogger(__name__)


class CustomDomainAttachError(Exception):
    """Base for every attach failure. infrastructure-service must not mark the domain
    VALIDATED, and must not have attached anything, when this (or a subclass) is raised."""


class CustomDomainNotFound(CustomDomainAttachError):
    """The infrastructure or application named in the request doesn't exist, or the
    application isn't under that infrastructure."""


class CustomDomainNotReady(CustomDomainAttachError):
    """The infra's ALB/listeners aren't provisioned yet — nothing to attach to."""


class CustomDomainConflict(CustomDomainAttachError):
    """This hostname is already routed to a different application. Should be unreachable
    in practice — infrastructure-service's partial unique index on VALIDATED CustomDomain
    rows already enforces this before this call is ever made — kept as defense in depth."""


class SniCapReached(CustomDomainAttachError):
    """The listener already carries as many SNI certificates as this platform will place
    on it (see settings.MAX_SNI_CERTIFICATES_PER_LISTENER)."""


def _resolve_https_listener(infra):
    from aws.alb import ALBClient
    from aws.session import create_boto3_session

    environment = infra.environments.first()
    if environment is None or not environment.alb_arn:
        raise CustomDomainNotReady("Infrastructure has no ALB provisioned yet")

    session = create_boto3_session(infra)
    alb = ALBClient(session)
    https_listener_arn = alb.get_listener_arn(environment.alb_arn, port=443)
    http_listener_arn = alb.get_listener_arn(environment.alb_arn, port=80)
    if not https_listener_arn or not http_listener_arn:
        raise CustomDomainNotReady("ALB is missing its :80 or :443 listener")
    return alb, https_listener_arn, http_listener_arn


def attach_custom_domain(*, infrastructure_id, application_id, hostname: str, cert_arn: str) -> dict:
    """Idempotent: a hostname already attached to the same (infrastructure, application)
    returns its existing rule ARNs without touching AWS again. Raises without attaching
    anything, or having already attached the certificate but not the rules, is not a state
    this function leaves behind on any exception path — see the ordering below."""
    from aws.alb import SniCertificateCapExceeded

    from api.models.application import Application
    from api.models.custom_domain_route import CustomDomainRoute
    from api.models.infrastructure import Infrastructure

    existing = CustomDomainRoute.objects.filter(hostname=hostname).first()
    if existing is not None:
        if str(existing.infrastructure_id) != str(infrastructure_id) or str(existing.application_id) != str(application_id):
            raise CustomDomainConflict(f"{hostname!r} is already routed to a different application")
        return {
            "host_forward_rule_arn": existing.host_forward_rule_arn,
            "host_redirect_rule_arn": existing.host_redirect_rule_arn,
        }

    try:
        infra = Infrastructure.objects.get(id=infrastructure_id)
    except Infrastructure.DoesNotExist as exc:
        raise CustomDomainNotFound("Infrastructure not found") from exc
    try:
        application = Application.objects.get(id=application_id, infrastructure_id=infrastructure_id)
    except Application.DoesNotExist as exc:
        raise CustomDomainNotFound("Application not found under this infrastructure") from exc

    alb, https_listener_arn, http_listener_arn = _resolve_https_listener(infra)

    if not alb.has_listener_certificate(https_listener_arn, cert_arn):
        if alb.count_listener_certificates(https_listener_arn) >= settings.MAX_SNI_CERTIFICATES_PER_LISTENER:
            raise SniCapReached(f"SNI certificate cap reached on listener {https_listener_arn}")
        try:
            alb.add_listener_certificate(https_listener_arn, cert_arn)
        except SniCertificateCapExceeded as exc:
            raise SniCapReached(str(exc)) from exc

    tags = app_tags(infrastructure_id, app_slug(application.name))
    host_forward_rule_arn = alb.create_host_forward_rule(
        https_listener_arn, application.target_group_arn, hostname, tags=tags,
    )
    host_redirect_rule_arn = alb.create_host_redirect_rule(http_listener_arn, hostname, tags=tags)

    route = CustomDomainRoute.objects.create(
        infrastructure_id=infrastructure_id, application_id=application_id, hostname=hostname,
        cert_arn=cert_arn, host_forward_rule_arn=host_forward_rule_arn,
        host_redirect_rule_arn=host_redirect_rule_arn,
    )
    return {"host_forward_rule_arn": route.host_forward_rule_arn, "host_redirect_rule_arn": route.host_redirect_rule_arn}


def _detach_route(route) -> None:
    """Best-effort on the AWS side, but the local row is always removed — a route this
    service can no longer account for must not linger and be reused by a later idempotent
    attach that assumes the ARNs it holds are still live."""
    from api.models.infrastructure import Infrastructure

    try:
        infra = Infrastructure.objects.get(id=route.infrastructure_id)
        environment = infra.environments.first()
        if environment is not None and environment.alb_arn:
            from aws.alb import ALBClient
            from aws.session import create_boto3_session

            alb = ALBClient(create_boto3_session(infra))
            https_listener_arn = alb.get_listener_arn(environment.alb_arn, port=443)
            if https_listener_arn:
                alb.remove_listener_certificate(https_listener_arn, route.cert_arn)
            alb.delete_rule(route.host_forward_rule_arn)
            if route.host_redirect_rule_arn:
                alb.delete_rule(route.host_redirect_rule_arn)
    except Exception:
        logger.warning("best-effort ALB detach failed for custom domain %s (row still removed)",
                        route.hostname, exc_info=True)
    finally:
        route.delete()


def detach_custom_domain(hostname: str) -> bool:
    """Idempotent: no route for this hostname is success (nothing to do), not an error —
    called both from infrastructure-service's disable path and from a retried request."""
    from api.models.custom_domain_route import CustomDomainRoute

    route = CustomDomainRoute.objects.filter(hostname=hostname).first()
    if route is None:
        return False
    _detach_route(route)
    return True


def detach_custom_domains_for_application(application_id) -> list[str]:
    """Called from the app-delete cleanup path (run_worker.py). No round trip to
    infrastructure-service is needed to discover what to detach — see CustomDomainRoute's
    docstring. Returns the detached hostnames, for the caller's best-effort notification
    of infrastructure-service (see notify_infrastructure_service_of_deleted_application)."""
    from api.models.custom_domain_route import CustomDomainRoute

    hostnames = []
    for route in list(CustomDomainRoute.objects.filter(application_id=application_id)):
        hostnames.append(route.hostname)
        _detach_route(route)
    return hostnames


def notify_infrastructure_service_of_deleted_application(infrastructure_id, application_id) -> None:
    """Best-effort: app deletion must never fail or roll back because
    infrastructure-service (a different service, a different database) couldn't be
    reached. An orphaned VALIDATED CustomDomain row whose rules are already gone is also
    caught by the periodic re-validation job there eventually (nothing will route to it,
    and its listener certificate is gone), or the owner can just delete it manually — this
    call only makes that cleanup immediate in the common case."""
    from shared.resilience.http_client import ResilientHttpClient

    client = ResilientHttpClient(
        name="InfrastructureServiceCustomDomainNotifyClient",
        base_url=settings.INFRASTRUCTURE_SERVICE_URL, timeout=5,
    )
    try:
        client.post(
            "/api/v1/internal/custom-domains/disable-for-application/",
            json={"infrastructure_id": str(infrastructure_id), "application_id": str(application_id)},
            headers={"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN},
            timeout=(2, 5),
        )
    except Exception:
        logger.warning(
            "failed to notify infrastructure-service that application %s was deleted "
            "(non-fatal — its custom domains will be caught by the re-validation sweep)",
            application_id, exc_info=True,
        )
