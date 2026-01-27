from django.db import models

class UserRole(models.TextChoices):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"
    SUPER_ADMIN = "super_admin"
