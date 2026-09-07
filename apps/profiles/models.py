"""Per-user preferences (language) linked to the built-in ``auth.User``.

Identity fields (first/last name, email) live on ``auth.User`` itself; this
profile only carries local preferences.  A profile row is created lazily on
first use (views use ``get_or_create``), so seed users never need profiles.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class UserProfile(TimeStampedModel):
    """One-to-one user preferences: only the interface language for now."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("user"),
    )
    language = models.CharField(
        _("language"),
        max_length=10,
        choices=settings.LANGUAGES,
        blank=True,
        default="",
        help_text=_(
            "Interface language. Leave empty to fall back to the browser "
            "language and the platform default."
        ),
    )

    class Meta:
        verbose_name = _("user profile")
        verbose_name_plural = _("user profiles")

    def __str__(self) -> str:
        return f"{self.user} — {self.language or settings.LANGUAGE_CODE}"
