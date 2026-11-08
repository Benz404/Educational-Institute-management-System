from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("portal.urls")),
]
handler403 = "portal.views.error_403"
handler404 = "portal.views.error_404"
