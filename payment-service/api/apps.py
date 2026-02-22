from django.apps import AppConfig
import logging
import threading
import os

class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        # We only want to start the consumer in the main process, not the reloader
        if os.environ.get('RUN_MAIN') == 'true' or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            logger = logging.getLogger(__name__)
            from api.messaging.consumer.user import AuthEventConsumer
            from api.messaging.consumer.infrastructure import InfraEventConsumer
            
            def start_auth_consumer():
                logger.info("Initializing AuthEventConsumer thread...")
                consumer = AuthEventConsumer()
                try:
                    consumer.start()
                except Exception as e:
                    logger.error(f"Critical error in AuthEventConsumer: {e}")

            def start_infra_consumer():
                logger.info("Initializing InfraEventConsumer thread...")
                consumer = InfraEventConsumer()
                try:
                    consumer.start()
                except Exception as e:
                    logger.error(f"Critical error in InfraEventConsumer: {e}")

            auth_thread = threading.Thread(target=start_auth_consumer, name="AuthEventConsumerThread", daemon=True)
            auth_thread.start()
            
            infra_thread = threading.Thread(target=start_infra_consumer, name="InfraEventConsumerThread", daemon=True)
            infra_thread.start()
            
            logger.info("Messaging background threads started successfully.")
