from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from api.services.payment_service import PaymentService
from api.repositories.user import UserRepository
import json
import logging

logger = logging.getLogger(__name__)

payment_service = PaymentService()
user_repo = UserRepository()

@csrf_exempt
def create_checkout_session(request):
    """
    Endpoint to initiate a Stripe Checkout Session.
    Expects X-User-ID header and JSON body with 'amount' and optionally 'infrastructure_id'.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)

    user_id = getattr(request.user, 'id', None)
    if not user_id:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    user = user_repo.get_user(user_id)
    if not user:
        logger.warning(f"User {user_id} not found for payment request")
        return JsonResponse({'error': 'User not found'}, status=404)

    try:
        data = json.loads(request.body)
        amount = data.get('amount') # amount in cents
        infra_id = data.get('infrastructure_id')

        if not amount:
            return JsonResponse({'error': 'Amount is required'}, status=400)

        session = payment_service.create_checkout_session(user, amount, infra_id)
        return JsonResponse({'checkout_url': session.url, 'session_id': session.id})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        logger.error(f"Error in create_checkout_session: {e}")
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def stripe_webhook(request):
    """
    Stripe Webhook handler to process payment events.
    """
    payload = request.body
    sig_header = request.headers.get('STRIPE_SIGNATURE')

    if not sig_header:
        logger.warning("Missing Stripe signature header")
        return HttpResponse(status=400)

    try:
        payment_service.handle_webhook(payload, sig_header)
        return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return HttpResponse(status=400)
