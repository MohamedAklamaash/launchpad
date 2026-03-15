from django.shortcuts import redirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from api.services.payment_service import PaymentService
from api.repositories.user import UserRepository
import logging

logger = logging.getLogger(__name__)

payment_service = PaymentService()
user_repo = UserRepository()


class CheckoutRequestSerializer(serializers.Serializer):
    amount = serializers.IntegerField(help_text="Amount in cents, e.g. 2000 = $20.00")
    infrastructure_id = serializers.UUIDField(help_text="Infrastructure this payment is for")

class CheckoutResponseSerializer(serializers.Serializer):
    checkout_url = serializers.CharField(help_text="Stripe-hosted checkout URL — redirect the user here")
    session_id = serializers.CharField(help_text="Stripe Checkout Session ID, e.g. cs_test_a1B2...")

class ProcessPaymentRequestSerializer(serializers.Serializer):
    amount = serializers.IntegerField(help_text="Amount in cents, e.g. 2000 = $20.00")
    payment_method_id = serializers.CharField(help_text="Stripe PaymentMethod ID, e.g. pm_card_visa")
    infrastructure_id = serializers.UUIDField(help_text="Infrastructure this payment is for")

class ProcessPaymentResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    payment_intent_id = serializers.CharField(help_text="Stripe PaymentIntent ID, e.g. pi_3OqX...")
    status = serializers.CharField(help_text="Stripe payment status, e.g. succeeded")

class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


@extend_schema(
    summary="Create a Stripe Checkout Session",
    description="Returns a Stripe-hosted checkout URL. Redirect the user to `checkout_url` to complete payment.",
    request=CheckoutRequestSerializer,
    responses={200: CheckoutResponseSerializer, 400: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
)
@api_view(['POST'])
def create_checkout_session(request):
    user = user_repo.get_user(request.user.sub)
    if not user:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        amount = request.data.get('amount')
        infra_id = request.data.get('infrastructure_id')
        if not amount or not infra_id:
            return Response({'error': 'amount and infrastructure_id are required'}, status=status.HTTP_400_BAD_REQUEST)
        session = payment_service.create_checkout_session(user, amount, infra_id)
        return Response({'checkout_url': session.url, 'session_id': session.id})
    except Exception as e:
        logger.error(f"Error in create_checkout_session: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Process a direct Stripe payment",
    description="Charges a saved payment method directly without a hosted checkout page.",
    request=ProcessPaymentRequestSerializer,
    responses={200: ProcessPaymentResponseSerializer, 400: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
)
@api_view(['POST'])
def process_payment(request):
    user = user_repo.get_user(request.user.sub)
    if not user:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        amount = request.data.get('amount')
        payment_method_id = request.data.get('payment_method_id')
        infra_id = request.data.get('infrastructure_id')
        if not amount or not payment_method_id or not infra_id:
            return Response({'error': 'amount, payment_method_id, and infrastructure_id are required'},
                            status=status.HTTP_400_BAD_REQUEST)
        result = payment_service.process_direct_payment(user, amount, payment_method_id, infra_id)
        return Response(result, status=status.HTTP_200_OK if result.get('success') else status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Error in process_payment: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Stripe webhook receiver",
    description="Called by Stripe. Must include the `Stripe-Signature` header for payload verification. Handles events: `checkout.session.completed`, `payment_intent.succeeded`, `payment_intent.payment_failed`.",
    request=None,
    responses={200: None, 400: None},
)
@api_view(['POST'])
@permission_classes([AllowAny])
def stripe_webhook(request):
    sig_header = request.headers.get('STRIPE_SIGNATURE') or request.headers.get('Stripe-Signature')
    if not sig_header:
        return Response(status=status.HTTP_400_BAD_REQUEST)
    try:
        payment_service.handle_webhook(request.body, sig_header)
        return Response(status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Payment success redirect",
    description="Stripe redirects here after successful checkout. Verifies the session then redirects to the frontend success page.",
    parameters=[OpenApiParameter("session_id", OpenApiTypes.STR, OpenApiParameter.QUERY,
                                 required=False, description="Stripe Checkout Session ID")],
    responses={302: None},
)
@api_view(['GET'])
@permission_classes([AllowAny])
def payment_success(request):
    session_id = request.query_params.get('session_id')
    if session_id:
        try:
            payment_service.verify_session(session_id)
        except Exception as e:
            logger.error(f"Failed to verify session {session_id}: {e}")
    from api.common.env.application import app_config
    return redirect(f"{app_config.frontend_url}/payment/success?session_id={session_id}")


@extend_schema(
    summary="Payment cancel redirect",
    description="Stripe redirects here when the user cancels checkout. Redirects to the frontend cancel page.",
    responses={302: None},
)
@api_view(['GET'])
@permission_classes([AllowAny])
def payment_cancel(request):
    from api.common.env.application import app_config
    return redirect(f"{app_config.frontend_url}/payment/cancel")
