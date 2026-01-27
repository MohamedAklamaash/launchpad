from django.apps import AppConfig
import threading
import os

class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        # We only want to start the consumer in the main process, not the reloader
        if os.environ.get('RUN_MAIN') == 'true' or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            from api.messaging.consumer.consumer import AuthEventConsumer
            import logging
            logger = logging.getLogger(__name__)
            
            def start_consumer():
                logger.info("Initializing AuthEventConsumer thread...")
                consumer = AuthEventConsumer()
                try:
                    consumer.start()
                except Exception as e:
                    logger.error(f"Critical error in AuthEventConsumer: {e}")

            thread = threading.Thread(target=start_consumer, name="AuthEventConsumerThread", daemon=True)
            thread.start()
            logger.info("AuthEventConsumer background thread started successfully.")
