"""
ASGI config for core project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from shared.process_role import is_dns_writer_role

if is_dns_writer_role():
    raise RuntimeError(
        "core.asgi refuses to start: LAUNCHPAD_PROCESS_ROLE=dns_writer must run only via "
        "`manage.py run_dns_writer` / `manage.py sweep_platform_dns`, never as a web "
        "server — see api/common/envs/application.py."
    )

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

application = get_asgi_application()
