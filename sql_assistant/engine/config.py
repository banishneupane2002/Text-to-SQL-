"""
Engine configuration module.
Loads settings from Django settings or environment variables.
"""
import os
from django.conf import settings

def get_groq_api_key() -> str:
    return getattr(settings, 'GROQ_API_KEY', os.getenv('GROQ_API_KEY', ''))

def get_groq_model() -> str:
    return getattr(settings, 'GROQ_MODEL', os.getenv('GROQ_MODEL', 'auto'))

def get_mssql_config() -> dict:
    return {
        "server": getattr(settings, 'MSSQL_SERVER', os.getenv('MSSQL_SERVER', r'.\SQLEXPRESS')),
        "database": getattr(settings, 'MSSQL_DATABASE', os.getenv('MSSQL_DATABASE', 'financial')),
        "driver": getattr(settings, 'MSSQL_DRIVER', os.getenv('MSSQL_DRIVER', 'ODBC Driver 17 for SQL Server')),
        "trusted_connection": getattr(settings, 'MSSQL_TRUSTED_CONNECTION', os.getenv('MSSQL_TRUSTED_CONNECTION', 'yes')),
    }

def get_sqlite_fallback_path() -> str:
    default_path = os.path.join(
        str(settings.BASE_DIR.parent), 'text-to-sql', 'financial.sqlite'
    ) if hasattr(settings, 'BASE_DIR') else 'financial.sqlite'
    return getattr(settings, 'SQLITE_FALLBACK_PATH', os.getenv('SQLITE_FALLBACK_PATH', default_path))

