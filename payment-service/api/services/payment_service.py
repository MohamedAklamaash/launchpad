import stripe
from api.common.env.application import app_config
from api.repositories.billing import BillingRepository
from api.repositories.infrastructure import InfrastructureRepository
from api.common.enums.billing_status import BillingStatus
import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

stripe.api_key = app_config.stripe_secret_key

class PaymentService:
    def __init__(self):
        self.billing_repo = BillingRepository()
        self.infra_repo = InfrastructureRepository()

    def create_checkout_session(self, user, amount, infrastructure_id=None):
        try:
            # 1. Validate Infrastructure existence
            infra = self.infra_repo.get_infrastructure(infrastructure_id)
            if not infra:
                raise ValueError(f"Infrastructure {infrastructure_id} not found in payments database")

            billing = self._create_pending_billing(user, amount, infrastructure_id)
            now = timezone.now()
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
                success_url=f'{app_config.backend_url}/api/v1/payments/success?session_id={{CHECKOUT_SESSION_ID}}',
                cancel_url=f'{app_config.backend_url}/api/v1/payments/cancel',
                client_reference_id=str(billing.id),
                customer_email=user.email,
            )
            
            logger.info(f"Created Stripe checkout session {session.id} for user {user.id}")
            return session
        except Exception as e:
            logger.error(f"Error creating Stripe checkout session: {e}")
            raise

    def process_direct_payment(self, user, amount, payment_method_id, infrastructure_id=None):
        """
        Process a direct payment using Stripe PaymentIntent.
        """
        try:
            # 1. Validate Infrastructure existence
            infra = self.infra_repo.get_infrastructure(infrastructure_id)
            if not infra:
                return {"success": False, "error": f"Infrastructure {infrastructure_id} not found in payments database"}

            billing = self._create_pending_billing(user, amount, infrastructure_id)

            # Create a PaymentIntent
            intent = stripe.PaymentIntent.create(
                amount=int(amount),
                currency='usd',
                payment_method=payment_method_id,
                customer=None, # Could link to Stripe customer if we had one
                confirm=True,
                off_session=True, # Allow processing without user being on-session
                description=f'Direct Payment for Infrastructure Usage - {timezone.now().strftime("%B %Y")}',
                metadata={"billing_id": str(billing.id)},
                return_url=f'{app_config.backend_url}/api/v1/payments/success' # Redirect back to backend first
            )

            if intent.status == 'succeeded':
                self.billing_repo.update_billing_status(billing.id, BillingStatus.COMPLETED)
                logger.info(f"Direct payment succeeded for billing {billing.id}")
                return {"success": True, "billing_id": str(billing.id), "intent_id": intent.id}
            else:
                logger.warning(f"Direct payment intent {intent.id} has status {intent.status}")
                return {"success": False, "status": intent.status, "billing_id": str(billing.id)}

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error in process_direct_payment: {e}")
            if 'billing' in locals():
                self.billing_repo.update_billing_status(billing.id, BillingStatus.FAILED)
            return {"success": False, "error": str(e), "billing_id": str(billing.id) if 'billing' in locals() else None}
        except Exception as e:
            logger.error(f"Unexpected error in process_direct_payment: {e}")
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

    def verify_session(self, session_id):
        """
        Verify a Stripe checkout session and update billing status.
        """
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            billing_id = session.get('client_reference_id')
            
            if billing_id:
                if session.payment_status == 'paid':
                    self.billing_repo.update_billing_status(billing_id, BillingStatus.COMPLETED)
                    logger.info(f"Verified session {session_id}: Billing {billing_id} updated to COMPLETED")
                elif session.status == 'expired':
                    self.billing_repo.update_billing_status(billing_id, BillingStatus.FAILED)
                    logger.warning(f"Verified session {session_id}: Session expired, Billing {billing_id} updated to FAILED")
            
            return session
        except Exception as e:
            logger.error(f"Error verifying Stripe session {session_id}: {e}")
            raise

    def _create_pending_billing(self, user, amount, infrastructure_id):
        """
        Helper to create a pending billing record.
        """
        now = timezone.now()
        billing_data = {
            "user_id": user.id,
            "amount": amount / 100.0,
            "month": now.month,
            "year": now.year,
            "status": BillingStatus.PENDING,
            "metadata": {"infrastructure_id": str(infrastructure_id)} if infrastructure_id else {}
        }
        return self.billing_repo.create_billing(billing_data)
