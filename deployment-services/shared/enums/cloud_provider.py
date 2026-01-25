from django.db import models

class CloudProvider(models.TextChoices):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    DIGITAL_OCEAN = "digital_ocean"
    ALIBABA_CLOUD = "alibaba_cloud"