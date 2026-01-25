from django.contrib import admin

from api.models.infrastructure import Infrastructure
from api.models.user import User

admin.site.register(User)
admin.site.register(Infrastructure)