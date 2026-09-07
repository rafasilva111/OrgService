"""Per-user locale activation.

``LocaleMiddleware`` (which runs before this one) picks a language from the
session / cookie / ``Accept-Language`` header.  This middleware then overrides
it for signed-in users who stored a preference in ``profiles.UserProfile``,
making the choice follow the account across sessions and devices.
"""

from django.utils import translation


class UserLanguageMiddleware:
    """Activate the signed-in user's stored language, if any."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile is not None and profile.language:
                translation.activate(profile.language)
                request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)
