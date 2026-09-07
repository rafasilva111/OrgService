"""Audit: immutable record of relevant changes (compliance/accountability).

Uses a ``GenericForeignKey`` to record *what* changed without importing every
domain app, plus denormalized organization/service for filtering.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """Immutable log entry: who did what to which resource, before/after."""

    _AUTH_ORG_FIELD = "organization"
    _AUTH_SERVICE_FIELD = None

    action = models.CharField(_("action"), max_length=128, db_index=True)
    actor_person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    actor_user = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )

    # Denormalized scope for authorization-aware queries.
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )

    object_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    object = GenericForeignKey("object_type", "object_id")

    old_value = models.JSONField(_("old value"), null=True, blank=True)
    new_value = models.JSONField(_("new value"), null=True, blank=True)

    class Meta:
        verbose_name = _("audit log")
        verbose_name_plural = _("audit logs")

        indexes = [
            models.Index(fields=["object_type", "object_id", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["actor_user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_user or self.actor_person} @ {self.created_at}"
