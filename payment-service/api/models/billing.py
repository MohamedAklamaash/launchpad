from django.db import models
from api.common.enums.billing_status import BillingStatus
from django.utils import timezone
from api.common.utils.uuid import uuid7_pk
from api.common.enums.billing_status import BillingPaymentMethod

def thirty_days_hence():
    return timezone.now() + timezone.timedelta(days=30)

class Billing(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid7_pk, editable=False)
    user_id = models.UUIDField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, default=BillingPaymentMethod.STRIPE.value)
    profit_percentage = models.DecimalField(max_digits=10, decimal_places=2, default=0.05)
    month = models.IntegerField()
    year = models.IntegerField()
    prev_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    status = models.CharField(max_length=20,choices=
        BillingStatus.choices,
        default=BillingStatus.PENDING
    )
    due_date = models.DateField(default=thirty_days_hence)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"{self.user_id} - {self.amount}"