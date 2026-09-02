from django.db import models


class ComputeType(models.TextChoices):
    ECS_FARGATE = "ecs_fargate"
    EKS = "eks"
