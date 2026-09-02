from django.urls import path

from api.views.health import health, liveness, readiness
from api.views.payment import (
    create_checkout_session,
    payment_cancel,
    payment_success,
    process_payment,
    stripe_webhook,
)

urlpatterns = [
    path('payments/checkout/', create_checkout_session, name='create-checkout-session'),
    path('payments/process-payment/', process_payment, name='process-payment'),
    path('payments/webhook/', stripe_webhook, name='stripe-webhook'),
    path('payments/success/', payment_success, name='payment-success'),
    path('payments/cancel/', payment_cancel, name='payment-cancel'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
]
