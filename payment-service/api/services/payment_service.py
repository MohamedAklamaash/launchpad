import stripe
from api.common.env.application import app_config
from api.repositories.billing import BillingRepository
from api.common.enums.billing_status import BillingStatus
import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

stripe.api_key = app_config.stripe_secret_key

class PaymentService:
    def __init__(self):
        self.billing_repo = BillingRepository()

    def create_checkout_session(self, user, amount, infrastructure_id=None):
        try:
            now = timezone.now()
            
            billing_data = {
                "user_id": user.id,
                "amount": amount / 100.0,
                "month": now.month,
                "year": now.year,
                "status": BillingStatus.PENDING,
                "metadata": {"infrastructure_id": str(infrastructure_id)} if infrastructure_id else {}
            }
            billing = self.billing_repo.create_billing(billing_data)

            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'Infrastructure Usage - {now.strftime("%B %Y")}',
                        },
                        'unit_amount': int(amount),
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=f'{app_config.frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}',
                cancel_url=f'{app_config.frontend_url}/payment/cancel',
                client_reference_id=str(billing.id),
                customer_email=user.email,
            )
            
            logger.info(f"Created Stripe checkout session {session.id} for user {user.id}")
            return session
        except Exception as e:
            logger.error(f"Error creating Stripe checkout session: {e}")
            raise

    def handle_webhook(self, payload, sig_header):
        endpoint_secret = app_config.stripe_webhook_secret
        
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, endpoint_secret
            )
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            raise e
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise e

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            billing_id = session.get('client_reference_id')
            if billing_id:
                self.billing_repo.update_billing_status(billing_id, BillingStatus.COMPLETED)
                logger.info(f"Payment completed and billing {billing_id} updated to COMPLETED")
        
        return True
