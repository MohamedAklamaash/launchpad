"""Claim/verify/list/delete orchestration for customer custom domains (F1b part 3b).

Owner-only for every write, matching DatabaseService's authz ladder exactly (invited
ADMINs are refused, same as database management) — see plan/F1b-tls-activation.md's Part
3b design section for the full authz decision and the reasoning behind the synchronous
internal-HTTP split with application-service (SNI cert attach/detach, ALB rules).

Cross-service note: `application_id` is verified by calling application-service's existing
`GET /api/v1/applications/{id}/` with the caller's own forwarded Authorization header — the
same "second, independent authorization check, not just trust-the-caller hop" pattern
exit_export.py uses, not a new internal-only endpoint. No local Application model or FK
exists in this service.
"""
import logging
import uuid

from api.cloud_providers.aws.authenticate import assume_role_credentials_only
from api.common.envs.application import app_config
from api.mock.aws_fixtures import resolve_region
from api.models.custom_domain import (
    MAX_CONSECUTIVE_VERIFICATION_FAILURES,
    CustomDomain,
    HostnameAlreadyValidatedError,
    IllegalStatusTransitionError,
    InvalidHostnameError,
    ReservedSuffixError,
)
from api.models.infrastructure import Infrastructure
from api.repositories.infrastructure import InfrastructureRepository
from api.services import custom_domain_cert, custom_domain_dns
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from shared.enums.orchestrator import ComputeType
from shared.mode import is_dev_mode
from shared.resilience.http_client import ResilientHttpClient

logger = logging.getLogger(__name__)

__all__ = ["CustomDomainConflictError", "CustomDomainService"]


class CustomDomainConflictError(ValueError):
    """Maps to 409 — distinct from the plain ValueError -> 400 mapping every other bad
    input in this service uses."""


class CustomDomainUpstreamError(RuntimeError):
    """application-service could not be reached, or answered with something this service
    doesn't know how to interpret."""


def _require_valid_uuid(value, not_found_message):
    try:
        uuid.UUID(str(value))
    except ValueError:
        raise LookupError(not_found_message)


_app_service_client = ResilientHttpClient(
    name="ApplicationServiceCustomDomainClient", base_url=settings.APPLICATION_SERVICE_URL, timeout=6,
)
_app_service_internal_client = ResilientHttpClient(
    name="ApplicationServiceCustomDomainInternalClient", base_url=settings.APPLICATION_SERVICE_URL, timeout=8,
)


class CustomDomainService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()

    # ── shared lookups ──────────────────────────────────────────────────────────

    def _get_infra_or_raise(self, user_id, infra_id):
        _require_valid_uuid(infra_id, "Infrastructure not found")
        infra = self.infra_repo.get_by_id(user_id, infra_id)
        if not infra:
            raise LookupError("Infrastructure not found")
        return infra

    def _require_owner(self, user_id, infra):
        if str(infra.user_id) != str(user_id):
            raise PermissionError("Only the infrastructure owner can manage custom domains")

    def _get_domain_or_raise(self, infra, domain_id):
        _require_valid_uuid(domain_id, "Custom domain not found")
        domain = CustomDomain.objects.filter(id=domain_id, infrastructure=infra).first()
        if domain is None:
            raise LookupError("Custom domain not found")
        return domain

    def _credentials_and_region(self, infra):
        credentials = assume_role_credentials_only(infra)
        region = resolve_region(infra)
        dev_mode = is_dev_mode(app_config.mode)
        return credentials, region, dev_mode

    # ── application_id verification (cross-service) ────────────────────────────

    def _verify_application(self, infra_id, application_id, authorization_header) -> None:
        headers = {"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN}
        if authorization_header:
            headers["Authorization"] = authorization_header
        try:
            response = _app_service_client.get(
                f"/api/v1/applications/{application_id}/", headers=headers,
                timeout=(2, 6), allow_redirects=False,
            )
        except Exception as e:
            raise CustomDomainUpstreamError(str(type(e).__name__)) from e
        finally:
            _app_service_client.session.cookies.clear()

        if response.status_code == 404:
            raise LookupError("Application not found")
        if response.status_code != 200:
            raise CustomDomainUpstreamError(f"unexpected status {response.status_code} from application-service")
        data = response.json()
        if str(data.get("infrastructure_id")) != str(infra_id):
            raise LookupError("Application not found")

    # ── application-service attach/detach (cross-service) ──────────────────────

    def _attach(self, infra_id, application_id, hostname, cert_arn) -> dict:
        try:
            response = _app_service_internal_client.post(
                "/api/v1/internal/custom-domains/attach/",
                json={
                    "infrastructure_id": str(infra_id), "application_id": str(application_id),
                    "hostname": hostname, "cert_arn": cert_arn,
                },
                headers={"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN},
                timeout=(2, 8),
            )
        except Exception as e:
            raise CustomDomainUpstreamError(str(type(e).__name__)) from e
        if response.status_code == 409:
            raise CustomDomainConflictError(response.json().get("error", "Could not attach custom domain"))
        if response.status_code != 200:
            raise CustomDomainUpstreamError(
                f"application-service attach failed with status {response.status_code}: {response.text}"
            )
        return response.json()

    def _detach(self, hostname) -> None:
        try:
            _app_service_internal_client.post(
                "/api/v1/internal/custom-domains/detach/",
                json={"hostname": hostname},
                headers={"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN},
                timeout=(2, 8),
            )
        except Exception:
            logger.warning("best-effort detach failed for %r (will be retried by the next teardown attempt)",
                            hostname, exc_info=True)

    # ── list ────────────────────────────────────────────────────────────────────

    def list_domains(self, user_id, infra_id):
        infra = self._get_infra_or_raise(user_id, infra_id)
        domains = CustomDomain.objects.filter(infrastructure=infra).select_related('infrastructure').order_by('-created_at')
        return infra, list(domains)

    def backfill_validation_record(self, infra, domain) -> dict | None:
        """Single-shot on-demand fetch for a claim response that didn't get the ACM
        validation record in time — never loops (see custom_domain_cert's module
        docstring)."""
        if not domain.cert_arn:
            return None
        credentials, region, dev_mode = self._credentials_and_region(infra)
        try:
            return custom_domain_cert.describe_validation_record(
                domain.cert_arn, domain.hostname, credentials=credentials, region=region,
                infra_is_mock=infra.is_mock, dev_mode=dev_mode,
            )
        except Exception:
            logger.warning("validation-record backfill failed for %s (non-fatal)", domain.hostname, exc_info=True)
            return None

    # ── claim ───────────────────────────────────────────────────────────────────

    def claim_domain(self, user_id, infra_id, application_id, hostname, authorization_header=None):
        infra = self._get_infra_or_raise(user_id, infra_id)
        self._require_owner(user_id, infra)

        if infra.compute_type == ComputeType.EKS:
            raise ValueError(
                "Custom domains require ECS Fargate host mode — EKS custom domains are not yet enabled"
            )

        _require_valid_uuid(application_id, "Application not found")
        self._verify_application(infra_id, application_id, authorization_header)

        with transaction.atomic():
            locked_infra = Infrastructure.objects.select_for_update().get(id=infra.id)
            pending_count = CustomDomain.objects.filter(
                infrastructure=locked_infra, status='PENDING',
            ).select_for_update().count()
            if pending_count >= settings.MAX_PENDING_CUSTOM_DOMAINS_PER_INFRA:
                raise CustomDomainConflictError(
                    f"This infrastructure already has {pending_count} pending custom-domain "
                    "claims — resolve or delete one before claiming another"
                )
            try:
                domain, ownership_token = CustomDomain.reserve(hostname, locked_infra, application_id)
            except (ReservedSuffixError, InvalidHostnameError) as exc:
                raise ValueError(str(exc)) from exc

        # ACM RequestCertificate runs outside the row lock — a synchronous AWS call is not
        # something a DB transaction should hold a lock across.
        credentials, region, dev_mode = self._credentials_and_region(infra)
        cert_arn = custom_domain_cert.request_certificate(
            domain.hostname, domain.id, timezone.now(), credentials=credentials, region=region,
            infra_is_mock=infra.is_mock, dev_mode=dev_mode,
        )
        domain.cert_arn = cert_arn
        domain.save(update_fields=['cert_arn'])
        validation_record = custom_domain_cert.poll_for_validation_record(
            cert_arn, domain.hostname, credentials=credentials, region=region,
            infra_is_mock=infra.is_mock, dev_mode=dev_mode,
        )
        return domain, ownership_token, validation_record

    # ── verify ──────────────────────────────────────────────────────────────────

    def verify_domain(self, user_id, infra_id, domain_id):
        infra = self._get_infra_or_raise(user_id, infra_id)
        self._require_owner(user_id, infra)
        domain = self._get_domain_or_raise(infra, domain_id)

        credentials, region, dev_mode = self._credentials_and_region(infra)

        with transaction.atomic():
            # Locks this infra's row for the duration of the SNI-cap check + attach below
            # — see the Part 3b design note on why that check needs to be serialized here
            # rather than merely inside application-service's own attach call.
            Infrastructure.objects.select_for_update().get(id=infra.id)
            locked = CustomDomain.objects.select_for_update().get(pk=domain.pk)

            if locked.status != 'PENDING':
                raise ValueError(f"Custom domain is not pending verification (status={locked.status})")
            if locked.is_expired:
                raise ValueError(f"Claim on {locked.hostname!r} expired at {locked.expires_at} — delete and re-claim")
            if not locked.cert_arn:
                raise ValueError("Certificate request has not completed yet — try again shortly")

            if not custom_domain_dns.verify_ownership_token(locked, infra_is_mock=infra.is_mock, dev_mode=dev_mode):
                raise ValueError(
                    f"TXT record at _launchpad-challenge.{locked.hostname} not found or does not match"
                )

            cert_status = custom_domain_cert.certificate_status(
                locked.cert_arn, credentials=credentials, region=region,
                infra_is_mock=infra.is_mock, dev_mode=dev_mode,
            )
            if cert_status != "ISSUED":
                raise ValueError(f"Certificate is not yet issued (status={cert_status}) — publish its validation CNAME and try again")

            self._attach(infra_id, locked.application_id, locked.hostname, locked.cert_arn)

            try:
                locked.mark_validated()
            except HostnameAlreadyValidatedError:
                self._detach(locked.hostname)
                raise CustomDomainConflictError(
                    f"{locked.hostname!r} was just validated on another account"
                ) from None

        return locked

    # ── delete / disable ────────────────────────────────────────────────────────

    def delete_domain(self, user_id, infra_id, domain_id):
        infra = self._get_infra_or_raise(user_id, infra_id)
        self._require_owner(user_id, infra)
        domain = self._get_domain_or_raise(infra, domain_id)
        self._teardown(infra, domain)
        return domain

    def _teardown(self, infra, domain) -> None:
        """Idempotent — safe to call on a domain already DISABLED (a retried request, or
        the periodic re-validation job racing an owner-triggered delete)."""
        if domain.status == 'DISABLED':
            return
        was_validated = domain.status == 'VALIDATED'
        if was_validated:
            self._detach(domain.hostname)
        if domain.cert_arn:
            credentials, region, dev_mode = self._credentials_and_region(infra)
            custom_domain_cert.delete_certificate(
                domain.cert_arn, credentials=credentials, region=region,
                infra_is_mock=infra.is_mock, dev_mode=dev_mode,
            )
        if was_validated:
            try:
                domain.mark_verification_failed()
            except IllegalStatusTransitionError:
                pass  # raced with another disable — already DISABLED, nothing left to do
        else:
            domain.delete()

    def teardown_for_infrastructure(self, infra) -> None:
        """Called from infra destroy (TerraformWorker._pre_destroy_cleanup) and from
        complete-exit — every CustomDomain on this infra, VALIDATED or PENDING, must leave
        nothing attached. Never raises: a single domain's teardown failing must not block
        the platform-DNS teardown path (H2) or the destroy/exit flow itself."""
        for domain in CustomDomain.objects.filter(infrastructure=infra).exclude(status='DISABLED'):
            try:
                self._teardown(infra, domain)
            except Exception:
                logger.warning(
                    "custom-domain teardown failed for %s on infra %s (non-fatal, will be retried)",
                    domain.hostname, infra.id, exc_info=True,
                )

    def disable_for_application(self, infra_id, application_id) -> None:
        """Called from the internal endpoint application-service hits after it has already
        detached its own rules for a deleted application — this side only needs to delete
        the ACM certificate (application-service's detach already happened) and mark the
        row DISABLED. Never calls back into application-service's detach (would be a no-op
        against rows already gone, and application-service's cleanup already ran it)."""
        infra = Infrastructure.objects.filter(id=infra_id).first()
        if infra is None:
            return
        domains = CustomDomain.objects.filter(
            infrastructure_id=infra_id, application_id=application_id,
        ).exclude(status='DISABLED')
        for domain in domains:
            try:
                if domain.cert_arn:
                    credentials, region, dev_mode = self._credentials_and_region(infra)
                    custom_domain_cert.delete_certificate(
                        domain.cert_arn, credentials=credentials, region=region,
                        infra_is_mock=infra.is_mock, dev_mode=dev_mode,
                    )
                if domain.status == 'VALIDATED':
                    domain.mark_verification_failed()
                else:
                    domain.delete()
            except Exception:
                logger.warning(
                    "disable_for_application teardown failed for %s (non-fatal)",
                    domain.hostname, exc_info=True,
                )

    # ── periodic re-validation + expired-claim sweep ────────────────────────────

    def sweep_expired_claims(self, *, time_budget_seconds: float = 20) -> None:
        """PENDING claims past their 72h TTL — a claim that only ever reached
        RequestCertificate and never got its CNAME published would otherwise leave a
        PENDING_VALIDATION certificate in the customer's account forever. Bounded per
        call, same pattern as run_worker.py's TLS PENDING re-check — remaining rows are
        picked up on the next tick, not held past a time budget."""
        deadline = timezone.now().timestamp() + time_budget_seconds
        expired = CustomDomain.objects.filter(
            status='PENDING', expires_at__lt=timezone.now(),
        ).select_related('infrastructure')
        for domain in expired:
            if timezone.now().timestamp() > deadline:
                logger.info("expired-claim sweep hit its time budget; remaining rows deferred to next tick")
                break
            try:
                self._teardown(domain.infrastructure, domain)
            except Exception:
                logger.warning("expired-claim sweep failed for %s (non-fatal, retried next tick)",
                                domain.hostname, exc_info=True)

    def revalidate_validated_domains(self, *, time_budget_seconds: float = 20) -> None:
        """Re-runs the authoritative TXT check against every VALIDATED domain.
        MAX_CONSECUTIVE_VERIFICATION_FAILURES (3) consecutive misses disables it — a
        customer who lets a domain lapse (deletes the TXT record, moves DNS providers
        without re-publishing it, transfers the domain away) must not keep an ALB rule and
        an ACM certificate live for a hostname they no longer control. A single missed
        check does not disable anything: DNS hiccups and transient authoritative-NS
        outages are expected, not evidence of lost ownership. Bounded per call, same
        pattern as sweep_expired_claims/run_worker.py's TLS re-check."""
        deadline = timezone.now().timestamp() + time_budget_seconds
        validated = CustomDomain.objects.filter(status='VALIDATED').select_related('infrastructure')
        for domain in validated:
            if timezone.now().timestamp() > deadline:
                logger.info("custom-domain re-validation hit its time budget; remaining rows deferred to next tick")
                break
            infra = domain.infrastructure
            try:
                dev_mode = is_dev_mode(app_config.mode)
                ok = custom_domain_dns.verify_ownership_token(domain, infra_is_mock=infra.is_mock, dev_mode=dev_mode)
            except Exception:
                logger.warning("re-validation TXT check failed for %s (treated as a miss)",
                                domain.hostname, exc_info=True)
                ok = False

            if ok:
                domain.record_verification_success()
                continue

            should_disable = domain.record_verification_failure()
            logger.warning(
                "custom domain %s failed re-validation (%d/%d consecutive)",
                domain.hostname, domain.verification_failure_count, MAX_CONSECUTIVE_VERIFICATION_FAILURES,
            )
            if should_disable:
                try:
                    self._teardown(infra, domain)
                except Exception:
                    logger.warning("teardown after failed re-validation failed for %s (non-fatal, retried next tick)",
                                    domain.hostname, exc_info=True)
