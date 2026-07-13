from types import SimpleNamespace


class BrowserTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.headers.get("X-Zeus-Test-Tenant") == "ui-baseline":
            request.tenant = SimpleNamespace(
                name="UI Baseline",
                schema_name="ui-baseline",
            )
        return self.get_response(request)
