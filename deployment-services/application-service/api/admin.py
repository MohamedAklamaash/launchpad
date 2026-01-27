from django.contrib import admin

from api.models.user import User
from api.models.infrastructure import Infrastructure
from api.models.application import Application

admin.site.register(User)
admin.site.register(Infrastructure)
admin.site.register(Application)