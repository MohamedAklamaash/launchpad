from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/organizations", tags=["Organizations"])
invitation_router = APIRouter(prefix="/invitations", tags=["Organizations"])


class InvitableRole(str, Enum):
    admin = "admin"
    developer = "developer"
    viewer = "viewer"


class CreateInvitationBody(BaseModel):
    email: str = Field(max_length=254, example="teammate@example.com")
    role: InvitableRole = Field(description="Never owner: nobody can be invited as owner.")


class AcceptInvitationBody(BaseModel):
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")


@router.post("/{organization_id}/invitations", status_code=201,
             summary="Invite an email address to an organization",
             description="Behind ORGS_ENABLED (auth-service). Owner or admin of the "
                         "organization only; the role is resolved by auth-service from the "
                         "caller's active Membership, never from the request. The "
                         "invitation token is emailed and never returned.")
async def create_invitation(organization_id: UUID, body: CreateInvitationBody, request: Request):
    return await proxy_request(
        f"{settings.AUTH_SERVICE_URL}/api/v1/organizations/{organization_id}/invitations", request)


@router.get("/{organization_id}/invitations",
            summary="List the organization's pending invitations")
async def list_invitations(organization_id: UUID, request: Request):
    return await proxy_request(
        f"{settings.AUTH_SERVICE_URL}/api/v1/organizations/{organization_id}/invitations", request)


@router.delete("/{organization_id}/invitations/{invitation_id}", status_code=204,
               summary="Revoke a pending invitation")
async def revoke_invitation(organization_id: UUID, invitation_id: UUID, request: Request):
    return await proxy_request(
        f"{settings.AUTH_SERVICE_URL}/api/v1/organizations/{organization_id}"
        f"/invitations/{invitation_id}", request)


@invitation_router.post("/accept", summary="Accept an organization invitation",
                        description="Requires the caller's session Authorization bearer token "
                                    "AND the invitation token in the body. The token is "
                                    "body-only so it never lands in URLs or access logs. The "
                                    "session's verified email must equal the invited email.")
async def accept_invitation(body: AcceptInvitationBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/invitations/accept", request)
