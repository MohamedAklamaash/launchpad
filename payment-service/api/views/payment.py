from django.shortcuts import redirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status
from api.services.payment_service import PaymentService
from api.repositories.user import UserRepository
import json
import logging

logger = logging.getLogger(__name__)

payment_service = PaymentService()
user_repo = UserRepository()

@api_view(['POST'])
def create_checkout_session(request):
    """
    Endpoint to initiate a Stripe Checkout Session.
    """
    user_id = request.user.sub
    user = user_repo.get_user(user_id)
    if not user:
        logger.warning(f"User {user_id} not found for payment request")
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        data = request.data
        amount = data.get('amount') # amount in cents
        infra_id = data.get('infrastructure_id')

        if not amount or not infra_id:
            return Response({'error': 'amount and infrastructure_id are required'}, status=status.HTTP_400_BAD_REQUEST)

        session = payment_service.create_checkout_session(user, amount, infra_id)
        return Response({'checkout_url': session.url, 'session_id': session.id})
    except Exception as e:
        logger.error(f"Error in create_checkout_session: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def process_payment(request):
    user_id = request.user.sub
    user = user_repo.get_user(user_id)
    if not user:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        data = request.data
        amount = data.get('amount')
        payment_method_id = data.get('payment_method_id')
        infra_id = data.get('infrastructure_id')

        if not amount or not payment_method_id or not infra_id:
            return Response({'error': 'amount, payment_method_id, and infrastructure_id are required'}, status=status.HTTP_400_BAD_REQUEST)

        result = payment_service.process_direct_payment(user, amount, payment_method_id, infra_id)
        
        status_code = status.HTTP_200_OK if result.get('success') else status.HTTP_400_BAD_REQUEST
        return Response(result, status=status_code)

    except Exception as e:
        logger.error(f"Error in process_payment: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([AllowAny])
def stripe_webhook(request):
    payload = request.body
    sig_header = request.headers.get('STRIPE_SIGNATURE')

    if not sig_header:
        logger.warning("Missing Stripe signature header")
        return Response(status=status.HTTP_400_BAD_REQUEST)

    try:
        payment_service.handle_webhook(payload, sig_header)
        return Response(status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([AllowAny])
def payment_success(request):
    session_id = request.query_params.get('session_id')
    if session_id:
        try:
            payment_service.verify_session(session_id)
        except Exception as e:
            logger.error(f"Failed to verify session {session_id} on redirect: {e}")
            
    from api.common.env.application import app_config
    return redirect(f"{app_config.frontend_url}/payment/success?session_id={session_id}")

@api_view(['GET'])
@permission_classes([AllowAny])
def payment_cancel(request):
    from api.common.env.application import app_config
    return redirect(f"{app_config.frontend_url}/payment/cancel")
