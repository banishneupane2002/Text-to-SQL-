import os
import sys
import threading
import logging
from django.apps import AppConfig

logger = logging.getLogger("sql_assistant.apps")


class SqlAssistantConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sql_assistant'

    def ready(self):
        """Pre-warms the database metadata in the background on server boot."""
        # Only run when serving HTTP traffic (runserver), not during migrations or tests
        is_runserver = any('runserver' in arg for arg in sys.argv)
        # Prevent double-run by Django's auto-reloader
        is_main_worker = os.environ.get('RUN_MAIN') == 'true' or not is_runserver

        if is_runserver and is_main_worker:
            def warmup():
                try:
                    from .engine.db_manager import extract_live_metadata
                    logger.info("Pre-warming database schema in background...")
                    catalog = extract_live_metadata()
                    logger.info(f"Schema pre-warmed successfully: {len(catalog)} tables loaded into RAM.")
                except Exception as e:
                    logger.warning(f"Schema pre-warming failed: {e}")

            t = threading.Thread(target=warmup, daemon=True, name="SchemaWarmupThread")
            t.start()
