from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["Auth"])
user_router = APIRouter(prefix="/user", tags=["GitHub OAuth"])

class RegisterBody(BaseModel):
    email: str = Field(example="user@example.com")
    password: str = Field(min_length=6, example="secret123")
    user_name: str = Field(min_length=3, example="johndoe")
    infra_id: str = Field(example="018e1234-abcd-7000-8000-000000000001")
    role: str = Field(example="USER", description="ADMIN or USER")

class LoginBody(BaseModel):
    email: str = Field(example="user@example.com")
    password: str = Field(min_length=6, example="secret123")

class AuthTokens(BaseModel):
    accessToken: str
    refreshToken: str

class ForgotPasswordBody(BaseModel):
    email: str = Field(example="user@example.com")

class ForgotPasswordResponse(BaseModel):
    otp: str = Field(example="482910", description="Dev-only — not returned in production")

class VerifyResetOTPBody(BaseModel):
    email: str = Field(example="user@example.com")
    otp: str = Field(min_length=6, max_length=6, example="482910")

class ResetPasswordBody(BaseModel):
    token: str = Field(example="eyJhbGciOiJIUzI1NiJ9...")
    newPassword: str = Field(min_length=6, example="newSecret123")

class UpdatePasswordBody(BaseModel):
    email: str = Field(example="user@example.com")
    oldPassword: str = Field(min_length=6, example="oldSecret123")
    newPassword: str = Field(min_length=6, example="newSecret456")

class RefreshBody(BaseModel):
    token: str = Field(example="eyJhbGciOiJIUzI1NiJ9...")

class RevokeBody(BaseModel):
    userId: str = Field(example="018e1234-abcd-7000-8000-000000000001")

class SuccessResponse(BaseModel):
    success: bool

class GitHubUser(BaseModel):
    id: str
    email: str
    user_name: str
    profile_url: Optional[str] = None
    accessToken: str
    refreshToken: str


@router.post("/register", summary="Register an invited user",
             response_model=AuthTokens, status_code=201)
async def auth_register(body: RegisterBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/register", request)


@router.post("/login", summary="Login with email and password", response_model=AuthTokens)
async def auth_login(body: LoginBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/login", request)


@router.get("/authenticate-with-otp", summary="Verify email OTP after registration",
            response_model=AuthTokens,
            description="Query params: `email` and `otp` (6-digit code sent after registration).")
async def auth_otp(email: str, otp: str, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/authenticate-with-otp", request)


@router.post("/forgot-password", summary="Request a password reset OTP",
             response_model=ForgotPasswordResponse)
async def auth_forgot_password(body: ForgotPasswordBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/forgot-password", request)


@router.post("/verify-reset-otp", summary="Verify password reset OTP",
             response_model=SuccessResponse)
async def auth_verify_reset_otp(body: VerifyResetOTPBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/verify-reset-otp", request)


@router.post("/reset-password", summary="Reset password using reset token",
             response_model=SuccessResponse)
async def auth_reset_password(body: ResetPasswordBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/reset-password", request)


@router.post("/update-password", summary="Update password (authenticated)",
             response_model=SuccessResponse)
async def auth_update_password(body: UpdatePasswordBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/update-password", request)


@router.post("/refresh", summary="Refresh access token", response_model=AuthTokens)
async def auth_refresh(body: RefreshBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/refresh", request)


@router.post("/revoke", summary="Revoke all refresh tokens for a user", status_code=204)
async def auth_revoke(body: RevokeBody, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/auth/revoke", request)


@router.get("/github", summary="Initiate GitHub OAuth login",
            description="Redirects to GitHub authorization page. No body or params required.",
            status_code=302)
async def auth_github(request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/user/login", request)


@user_router.get("/login", summary="Initiate GitHub OAuth login",
                 description="Redirects to GitHub authorization page.", status_code=302)
async def user_github_login(request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/user/login", request)


@user_router.get("/callback", summary="GitHub OAuth callback",
                 description="GitHub redirects here with `code` query param. Exchanges it for tokens and redirects to frontend with `access_token` and `refresh_token` as query params.",
                 status_code=302)
async def user_github_callback(code: str, request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/user/callback", request)


@user_router.get("/me", summary="Get current authenticated user", response_model=GitHubUser)
async def user_me(request: Request):
    return await proxy_request(f"{settings.AUTH_SERVICE_URL}/api/v1/user/me", request)
