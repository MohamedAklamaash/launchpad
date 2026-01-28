from api.models.billing import Billing

class BillingRepository:
    def get_billing(self, billing_id):
        try:
            return Billing.objects.get(id=billing_id)
        except Billing.DoesNotExist:
            return None

    def create_billing(self, billing_data):
        return Billing.objects.create(**billing_data)

    def update_billing_status(self, billing_id, status):
        billing = self.get_billing(billing_id)
        if billing:
            billing.status = status
            billing.save()
        return billing
