from django.core.management.base import BaseCommand
from api.models import Application
import psycopg2
import os

class Command(BaseCommand):
    help = 'Sync existing applications from application service database'

    def handle(self, *args, **options):
        app_db_url = os.environ.get('APPLICATION_DB_URL')
        if not app_db_url:
            self.stdout.write(self.style.ERROR('APPLICATION_DB_URL not set'))
            return

        try:
            conn = psycopg2.connect(app_db_url)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, infrastructure_id, name, user_id 
                FROM api_application
            """)
            
            apps = cursor.fetchall()
            synced = 0
            
            for app_id, infra_id, name, user_id in apps:
                Application.objects.update_or_create(
                    id=app_id,
                    defaults={
                        'infrastructure_id': infra_id,
                        'name': name,
                        'user_id': user_id
                    }
                )
                synced += 1
            
            cursor.close()
            conn.close()
            
            self.stdout.write(self.style.SUCCESS(f'Successfully synced {synced} applications'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
