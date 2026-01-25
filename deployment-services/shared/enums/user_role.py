from django.db import models

class UserRole(models.TextChoices):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"
