from django.apps import AppConfig


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

            def start_infra_consumer():
                from api.messaging.consumers.infrastructure import InfraEventConsumer
                try:
                    logger.info("Initializing Application Service InfraEventConsumer...")
                    InfraEventConsumer().start()
                except Exception as e:
                    logger.error(f"Failed to start InfraEventConsumer: {e}")

            def start_auth_consumer():
                from api.messaging.consumers.user import AuthEventConsumer
                try:
                    logger.info("Initializing Application Service AuthEventConsumer...")
                    AuthEventConsumer().start()
                except Exception as e:
                    logger.error(f"Failed to start AuthEventConsumer: {e}")

            threading.Thread(target=start_infra_consumer, name="AppInfraConsumer", daemon=True).start()
            threading.Thread(target=start_auth_consumer, name="AppAuthConsumer", daemon=True).start()
            logger.info("Application Service messaging background threads started.")
