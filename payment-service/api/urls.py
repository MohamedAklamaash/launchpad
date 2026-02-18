from django.urls import path
from api.views.payment import create_checkout_session, stripe_webhook, process_payment, payment_success, payment_cancel
from api.views.health import health, liveness, readiness

urlpatterns = [
    path('checkout/', create_checkout_session, name='create-checkout-session'),
    path('process-payment/', process_payment, name='process-payment'),
    path('webhook/', stripe_webhook, name='stripe-webhook'),
    path('success/', payment_success, name='payment-success'),
    path('cancel/', payment_cancel, name='payment-cancel'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
]
