"""
WSGI config for core project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application
from shared.process_role import is_dns_writer_role

if is_dns_writer_role():
    raise RuntimeError(
        "core.wsgi refuses to start: LAUNCHPAD_PROCESS_ROLE=dns_writer must run only via "
        "`manage.py run_dns_writer` / `manage.py sweep_platform_dns`, never as a web "
        "server — see api/common/envs/application.py."
    )

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

application = get_wsgi_application()
