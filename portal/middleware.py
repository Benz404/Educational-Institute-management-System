from django.shortcuts import redirect

from .models import SiteConfig

OPEN_PREFIXES = ("/setup/", "/static/", "/admin/jsi18n")


class SetupRequiredMiddleware:
    """Until the one-time setup is finished, every page leads to the setup wizard."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith(OPEN_PREFIXES):
            if not SiteConfig.objects.filter(setup_complete=True).exists():
                return redirect("setup")
        return self.get_response(request)
