from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/payments", tags=["Payments"])


class CheckoutBody(BaseModel):
    amount: int = Field(example=2000, description="Amount in cents, e.g. 2000 = $20.00")
    infrastructure_id: str = Field(example="018e1234-abcd-7000-8000-000000000001")

class CheckoutResponse(BaseModel):
    checkout_url: str = Field(example="https://checkout.stripe.com/pay/cs_test_...",
                               description="Redirect the user to this URL to complete payment")
    session_id: str = Field(example="cs_test_a1B2c3D4e5F6...")

class ProcessPaymentBody(BaseModel):
    amount: int = Field(example=2000, description="Amount in cents, e.g. 2000 = $20.00")
    payment_method_id: str = Field(example="pm_card_visa", description="Stripe PaymentMethod ID")
    infrastructure_id: str = Field(example="018e1234-abcd-7000-8000-000000000001")

class ProcessPaymentResponse(BaseModel):
    success: bool
    payment_intent_id: str = Field(example="pi_3OqX...")
    status: str = Field(example="succeeded")

@router.post("/checkout", summary="Create a Stripe Checkout Session",
             response_model=CheckoutResponse)
async def payment_checkout(body: CheckoutBody, request: Request):
    """Returns a Stripe-hosted checkout URL. Redirect the user to `checkout_url` to complete payment."""
    return await proxy_request(f"{settings.PAYMENT_SERVICE_URL}/api/v1/payments/checkout/", request)


@router.post("/process", summary="Process a direct Stripe payment",
             response_model=ProcessPaymentResponse)
async def payment_process(body: ProcessPaymentBody, request: Request):
    """Charges a saved payment method directly without a hosted checkout page."""
    return await proxy_request(f"{settings.PAYMENT_SERVICE_URL}/api/v1/payments/process-payment/", request)


@router.post("/webhook", summary="Stripe webhook receiver", status_code=200)
async def payment_webhook(request: Request):
    """
    Called by Stripe. Must include the `Stripe-Signature` header.
    """
    return await proxy_request(f"{settings.PAYMENT_SERVICE_URL}/api/v1/payments/webhook/", request)


@router.get("/success", summary="Payment success redirect", status_code=302)
async def payment_success(session_id: Optional[str] = None, *, request: Request):
    """
    Query param `session_id` (optional) — Stripe Checkout Session ID.
    """
    return await proxy_request(f"{settings.PAYMENT_SERVICE_URL}/api/v1/payments/success/", request)


@router.get("/cancel", summary="Payment cancel redirect", status_code=302)
async def payment_cancel(request: Request):
    """Redirects to the frontend cancel page when user abandons Stripe checkout."""
    return await proxy_request(f"{settings.PAYMENT_SERVICE_URL}/api/v1/payments/cancel/", request)
