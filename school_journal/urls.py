"""Корневые маршруты проекта."""
from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

# Страницы ошибок: понятные пользователю и с блоком «Информация для разработчика».
handler400 = "journal.error_views.bad_request"
handler403 = "journal.error_views.permission_denied"
handler404 = "journal.error_views.page_not_found"
handler500 = "journal.error_views.server_error"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("journal.urls")),
]

# В режиме отладки (DEBUG=1) статику дополнительно отдаёт сам Django.
# В обычном режиме её раздаёт whitenoise (настройка STORAGES в settings.py).
if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
