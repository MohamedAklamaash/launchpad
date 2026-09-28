from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/infrastructures/{infra_id}/custom-domains", tags=["Custom Domains"])


class CustomDomainClaimBody(BaseModel):
    application_id: UUID
    hostname: str = Field(example="app.example.com", max_length=255)


class DnsRecord(BaseModel):
    name: str
    type: str
    value: str | None = None


class CustomDomainResponse(BaseModel):
    id: str
    application_id: str | None = None
    hostname: str
    status: str = Field(example="PENDING", description="PENDING | VALIDATED | DISABLED")
    expires_at: str
    last_verified_at: str | None = None
    created_at: str
    ownership_txt: DnsRecord = Field(description="TXT to publish; value is shown once, at claim time only")
    cname_to_edge: DnsRecord
    acm_validation: DnsRecord | None = None


@router.get("/", summary="List custom domains claimed on an infrastructure", response_model=list[CustomDomainResponse])
async def custom_domain_list(infra_id: UUID, request: Request):
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/custom-domains/", request,
    )


@router.post(
    "/", summary="Claim a custom domain", response_model=CustomDomainResponse, status_code=201,
)
async def custom_domain_claim(infra_id: UUID, body: CustomDomainClaimBody, request: Request):
    """Owner only. Returns the DNS records to publish in the customer's own DNS: an
    ownership TXT (shown once — save it now), the ACM validation CNAME, and a CNAME to the
    platform edge. Refused for EKS infrastructures (host mode is not yet enabled there)."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/custom-domains/", request,
    )


@router.post(
    "/{domain_id}/verify", summary="Verify a custom domain's ownership TXT and activate it",
    response_model=CustomDomainResponse,
)
async def custom_domain_verify(infra_id: UUID, domain_id: UUID, request: Request):
    """Owner only. A 409 here means the ALB's SNI certificate cap is reached or another
    account just validated the same hostname first."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/custom-domains/{domain_id}/verify/",
        request,
    )


@router.delete(
    "/{domain_id}", summary="Delete (or cancel) a custom domain", response_model=CustomDomainResponse,
)
async def custom_domain_delete(infra_id: UUID, domain_id: UUID, request: Request):
    """Owner only. A PENDING claim is removed outright; a VALIDATED domain is detached
    from the ALB, has its certificate deleted, and is marked DISABLED."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/custom-domains/{domain_id}/", request,
    )
