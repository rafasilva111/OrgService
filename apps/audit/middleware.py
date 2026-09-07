"""Request-context middleware for request-aware audit capture.

Runs after ``AuthenticationMiddleware`` so it can read ``request.user`` and
make it available to the audit signals attached to model ``save()`` hooks.
"""

from apps.audit.signals import clear_current_user, set_current_user


class AuditRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        set_current_user(user)
        try:
            response = self.get_response(request)
        finally:
            clear_current_user()
        return response
