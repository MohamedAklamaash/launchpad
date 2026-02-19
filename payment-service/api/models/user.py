from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from api.common.enums.user_role import UserRole

class UserManager(BaseUserManager):
    def create_user(self, email, user_name, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, user_name=user_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, user_name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", UserRole.ADMIN)

        return self.create_user(email, user_name, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, editable=False)
    email = models.EmailField(unique=True)
    user_name = models.CharField(max_length=150)
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(null=True, blank=True)
    invited_by = models.UUIDField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["user_name"]

    def __str__(self):
        return self.email