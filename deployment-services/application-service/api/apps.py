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
            from django.core.management import call_command
            
            def run_consumers():
                try:
                    call_command('consume_events')
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to start consumers: {e}")

            threading.Thread(target=run_consumers, daemon=True).start()
