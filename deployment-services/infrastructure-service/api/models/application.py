from django.db import models

class Application(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    infrastructure_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    user_id = models.CharField(max_length=255)
    
    class Meta:
        db_table = 'applications'
