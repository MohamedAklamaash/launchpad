from django.db import models

class BillingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"

class BillingPaymentMethod(models.TextChoices):
    STRIPE = "STRIPE", "Stripe"
    PAYPAL = "PAYPAL", "PayPal"
    UPI = "UPI", "UPI"