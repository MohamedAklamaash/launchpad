from django.db import models

class Infrastructure(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    user_id = models.UUIDField(null=True, blank=True) # Changed to UUIDField with null=True for resilience
    name = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=50, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name or self.id} (User: {self.user_id})"
