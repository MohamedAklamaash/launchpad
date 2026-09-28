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
import re

from aws.tags import app_tags
from django.conf import settings
from django.db import IntegrityError
from shared.validators.hostname import validate_hostname_syntax

from api.common.naming import app_slug

logger = logging.getLogger(__name__)

# arn:aws:acm:{region}:{account_id}:certificate/{id} — a coarse shape check, not a
# guarantee the certificate exists; the account-id segment is cross-checked against the
# calling infrastructure's own AWS account below (security review RECOMMENDED: this
# endpoint must not simply trust whatever ARN infrastructure-service hands it).
_CERT_ARN_RE = re.compile(r"^arn:aws:acm:[a-z0-9-]+:(\d{12}):certificate/[A-Za-z0-9-]+$")


class CustomDomainAttachError(Exception):
    """Base for every attach failure. infrastructure-service must not mark the domain
    VALIDATED, and must not have attached anything, when this (or a subclass) is raised."""


class CustomDomainNotFound(CustomDomainAttachError):
    """The infrastructure or application named in the request doesn't exist, or the
    application isn't under that infrastructure."""


class CustomDomainNotReady(CustomDomainAttachError):
    """The infra's ALB/listeners aren't provisioned yet, or the application has never
    deployed (no target group) — nothing to attach to."""


class CustomDomainConflict(CustomDomainAttachError):
    """This hostname is already routed to a different application. Should be unreachable
    in practice — infrastructure-service's partial unique index on VALIDATED CustomDomain
    rows already enforces this before this call is ever made — kept as defense in depth."""


class SniCapReached(CustomDomainAttachError):
    """The listener already carries as many SNI certificates as this platform will place
    on it (see settings.MAX_SNI_CERTIFICATES_PER_LISTENER)."""


def _validate_hostname(hostname: str) -> None:
    try:
        validate_hostname_syntax(hostname)
    except Exception as exc:
        raise CustomDomainAttachError(f"invalid hostname: {exc}") from exc


def _validate_cert_arn(cert_arn: str, infra) -> None:
    match = _CERT_ARN_RE.match(cert_arn or "")
    if not match:
        raise CustomDomainAttachError("cert_arn is not a well-formed ACM certificate ARN")
    # Mock certificates don't carry the real account id (see cert_bootstrap.FakeAcmClient
    # — always "000000000000"), so the cross-check only applies to real infrastructures.
    if not infra.is_mock and infra.code and match.group(1) != infra.code:
        raise CustomDomainAttachError("cert_arn does not belong to this infrastructure's AWS account")


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
    """Idempotent: a hostname already fully attached to the same (infrastructure,
    application) returns its existing rule ARNs without touching AWS again.

    Reservation-first (security review RECOMMENDED): the CustomDomainRoute row is created
    — with placeholder rule ARNs — BEFORE any AWS call, not after. This closes the window
    a purely-in-memory attempt would otherwise leave: a concurrent attach for the exact
    same hostname now collides on the row's unique hostname constraint immediately,
    rather than both callers independently reaching AWS and racing there instead. Any
    failure past this point compensates by removing whatever was actually attached (the
    SNI certificate, then any rule) before deleting the reservation — see the `except`
    block below.
    """
    from aws.alb import SniCertificateCapExceeded

    from api.models.application import Application
    from api.models.custom_domain_route import CustomDomainRoute
    from api.models.infrastructure import Infrastructure

    _validate_hostname(hostname)

    existing = CustomDomainRoute.objects.filter(hostname=hostname).first()
    if existing is not None:
        if str(existing.infrastructure_id) != str(infrastructure_id) or str(existing.application_id) != str(application_id):
            raise CustomDomainConflict(f"{hostname!r} is already routed to a different application")
        if existing.host_forward_rule_arn:
            return {
                "host_forward_rule_arn": existing.host_forward_rule_arn,
                "host_redirect_rule_arn": existing.host_redirect_rule_arn,
            }
        # A reservation left behind by a crashed previous attempt for this exact
        # (infra, app, hostname) — safe to resume rather than error.
        route = existing
    else:
        route = None

    try:
        infra = Infrastructure.objects.get(id=infrastructure_id)
    except Infrastructure.DoesNotExist as exc:
        raise CustomDomainNotFound("Infrastructure not found") from exc
    try:
        application = Application.objects.get(id=application_id, infrastructure_id=infrastructure_id)
    except Application.DoesNotExist as exc:
        raise CustomDomainNotFound("Application not found under this infrastructure") from exc

    if not application.target_group_arn:
        raise CustomDomainNotReady("Application has never deployed — no target group to route to yet")

    _validate_cert_arn(cert_arn, infra)

    if route is None:
        try:
            route = CustomDomainRoute.objects.create(
                infrastructure_id=infrastructure_id, application_id=application_id, hostname=hostname,
                cert_arn=cert_arn, host_forward_rule_arn="", host_redirect_rule_arn=None,
            )
        except IntegrityError:
            raise CustomDomainConflict(f"{hostname!r} is already being attached") from None

    cert_newly_attached = False
    host_forward_rule_arn = None
    try:
        alb, https_listener_arn, http_listener_arn = _resolve_https_listener(infra)

        if not alb.has_listener_certificate(https_listener_arn, cert_arn):
            if alb.count_listener_certificates(https_listener_arn) >= settings.MAX_SNI_CERTIFICATES_PER_LISTENER:
                raise SniCapReached(f"SNI certificate cap reached on listener {https_listener_arn}")
            try:
                alb.add_listener_certificate(https_listener_arn, cert_arn)
                cert_newly_attached = True
            except SniCertificateCapExceeded as exc:
                raise SniCapReached(str(exc)) from exc

        tags = app_tags(infrastructure_id, app_slug(application.name))
        host_forward_rule_arn = alb.create_host_forward_rule(
            https_listener_arn, application.target_group_arn, hostname, tags=tags,
        )
        host_redirect_rule_arn = alb.create_host_redirect_rule(http_listener_arn, hostname, tags=tags)
    except Exception:
        logger.warning("attach failed for %s, compensating (removing reservation and any partial AWS state)",
                        hostname, exc_info=True)
        if cert_newly_attached:
            try:
                alb.remove_listener_certificate(https_listener_arn, cert_arn)
            except Exception:
                logger.warning("compensating cert detach failed for %s (non-fatal)", hostname, exc_info=True)
        if host_forward_rule_arn:
            try:
                alb.delete_rule(host_forward_rule_arn)
            except Exception:
                logger.warning("compensating rule delete failed for %s (non-fatal)", hostname, exc_info=True)
        route.delete()
        raise

    route.cert_arn = cert_arn
    route.host_forward_rule_arn = host_forward_rule_arn
    route.host_redirect_rule_arn = host_redirect_rule_arn
    route.save(update_fields=['cert_arn', 'host_forward_rule_arn', 'host_redirect_rule_arn'])
    return {"host_forward_rule_arn": host_forward_rule_arn, "host_redirect_rule_arn": host_redirect_rule_arn}


_ALREADY_DETACHED_ERROR_CODES = {"LoadBalancerNotFound"}


def _is_already_detached_error(error_code: str, *, infra_tearing_down: bool) -> bool:
    """True for AWS errors that mean "there is nothing left to detach", so
    _detach_route should proceed to delete the route row instead of leaving it wedged
    in place forever (security review re-verification item 4).

    LoadBalancerNotFound always qualifies: the load balancer being gone means there is
    no listener, rule, or certificate to detach from, full stop. AccessDenied only
    qualifies when infra_tearing_down is set — an AssumeRole failure at any other time
    is more likely a real permission problem worth surfacing and retrying, not evidence
    the role was deliberately deleted post-exit."""
    if error_code in _ALREADY_DETACHED_ERROR_CODES:
        return True
    return error_code == "AccessDenied" and infra_tearing_down


def _detach_route(route, *, infra_tearing_down: bool = False) -> bool:
    """Returns True once the ALB side is confirmed clean, there was never anything to
    detach from, or the AWS error itself proves nothing is left to detach (see
    _is_already_detached_error) — the row is deleted in all three cases. A route left
    behind after any other failed AWS call must still be findable on the next retry
    (security review R2), not silently gone while its rule/certificate may still be
    live."""
    from botocore.exceptions import ClientError

    from api.models.infrastructure import Infrastructure

    try:
        infra = Infrastructure.objects.get(id=route.infrastructure_id)
        environment = infra.environments.first()
    except Infrastructure.DoesNotExist:
        infra, environment = None, None

    if infra is not None and environment is not None and environment.alb_arn:
        try:
            from aws.alb import ALBClient
            from aws.session import create_boto3_session

            alb = ALBClient(create_boto3_session(infra))
            https_listener_arn = alb.get_listener_arn(environment.alb_arn, port=443)
            if https_listener_arn:
                alb.remove_listener_certificate(https_listener_arn, route.cert_arn)
            if route.host_forward_rule_arn:
                alb.delete_rule(route.host_forward_rule_arn)
            if route.host_redirect_rule_arn:
                alb.delete_rule(route.host_redirect_rule_arn)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if not _is_already_detached_error(code, infra_tearing_down=infra_tearing_down):
                logger.warning("ALB detach failed for custom domain %s (will retry)", route.hostname, exc_info=True)
                return False
            logger.info(
                "custom domain %s: treating AWS error %s as already detached "
                "(infra_tearing_down=%s)", route.hostname, code, infra_tearing_down,
            )
        except Exception:
            logger.warning("ALB detach failed for custom domain %s (will retry)", route.hostname, exc_info=True)
            return False

    route.delete()
    return True


def detach_custom_domain(infrastructure_id, hostname: str, *, infra_tearing_down: bool = False) -> bool:
    """Idempotent: no route for this (infrastructure_id, hostname) is success (nothing
    to do), not an error. Scoped by both fields, not hostname alone (security review R2)
    — a hostname freed by a DISABLED row can be reclaimed by a different infrastructure,
    and a stale/retried detach call for the OLD owner must never touch the NEW owner's
    current attachment of the same hostname.

    infra_tearing_down is asserted by infrastructure-service, the only service whose
    Infrastructure row carries dns_teardown_requested_at/exited_at — see
    _is_already_detached_error."""
    from api.models.custom_domain_route import CustomDomainRoute

    route = CustomDomainRoute.objects.filter(infrastructure_id=infrastructure_id, hostname=hostname).first()
    if route is None:
        return True
    return _detach_route(route, infra_tearing_down=infra_tearing_down)


def detach_custom_domains_for_application(application_id) -> list[str]:
    """Called from the app-delete cleanup path (run_worker.py). No round trip to
    infrastructure-service is needed to discover what to detach — see CustomDomainRoute's
    docstring. Returns only the hostnames actually confirmed detached — a failed detach
    leaves its route row in place for a future retry and is not reported here."""
    from api.models.custom_domain_route import CustomDomainRoute

    detached = []
    for route in list(CustomDomainRoute.objects.filter(application_id=application_id)):
        hostname = route.hostname
        if _detach_route(route):
            detached.append(hostname)
    return detached


def notify_infrastructure_service_of_deleted_application(infrastructure_id, application_id) -> None:
    """Best-effort: app deletion must never fail or roll back because
    infrastructure-service (a different service, a different database) couldn't be
    reached. Called unconditionally on app delete (security review RECOMMENDED) — not
    only when a CustomDomainRoute existed here, since a still-PENDING domain (never
    attached, so no route in this service at all) also needs its certificate cleaned up
    on the infrastructure-service side, and this is the only signal that ever reaches it
    that the application is gone."""
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
