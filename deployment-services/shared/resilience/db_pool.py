import os
from typing import Dict, Any

def get_db_pool_config(db_config: Any, conn_max_age: int = None) -> Dict[str, Any]:
    """
    Returns a Django DATABASE configuration dictionary with pooling enabled.
    """
    if conn_max_age is None:
        conn_max_age = int(os.environ.get("DB_CONN_MAX_AGE", 600))
        
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": db_config.name,
        "USER": db_config.user_name,
        "PASSWORD": db_config.password,
        "HOST": db_config.host,
        "PORT": db_config.port,
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": {
            "sslmode": "require" if getattr(db_config, 'ssl', False) else "disable",
            "connect_timeout": 10,
        },
    }
