from django.urls import path
from api.views.payment import create_checkout_session, stripe_webhook

urlpatterns = [
    path('checkout/', create_checkout_session, name='create-checkout-session'),
    path('webhook/', stripe_webhook, name='stripe-webhook'),
]
