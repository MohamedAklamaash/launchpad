from django.db import models
from api.models.user import User

class Infrastructure(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='infrastructures')
    name = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=50, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name or self.id} ({self.user.email})"
