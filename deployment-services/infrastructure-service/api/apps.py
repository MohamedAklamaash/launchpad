from django.apps import AppConfig
import threading
import os

class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        """Start RabbitMQ consumers when the server starts."""
        import os
        import sys
        # Ensure it only runs in the main process, not the reloader
        if os.environ.get('RUN_MAIN') == 'true' or 'runserver' not in sys.argv:
            import threading
            import logging
            logger = logging.getLogger(__name__)
            
            def start_auth_consumer():
                from api.messaging.consumer.consumer import AuthEventConsumer
                try:
                    logger.info("Initializing Infrastructure Service AuthEventConsumer...")
                    AuthEventConsumer().start()
                except Exception as e:
                    logger.error(f"Critical error in AuthEventConsumer: {e}")

            threading.Thread(target=start_auth_consumer, name="InfraAuthConsumer", daemon=True).start()
            logger.info("Infrastructure Service messaging background thread started.")
