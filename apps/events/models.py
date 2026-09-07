"""Events: immutable records of things that happened.

Immutability: ``occurred_at`` and ``metadata`` are ``editable=False`` and there
is deliberately no ``save`` shortcut here — write once, read many. Events feed
audit trails, notifications, automation, workflow triggers and metrics later.

The subject is generic (``GenericForeignKey``) so any domain entity can emit
events without app-to-app imports.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class Event(TimeStampedModel):
    """Something that happened (RequestCreated, TaskCompleted, …)."""

    _AUTH_ORG_FIELD = "organization"
    _AUTH_SERVICE_FIELD = "service"

    # Denormalized scope for cheap filtering/authorization.
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="events",
        null=True,
        blank=True,
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )

    event_type = models.CharField(_("event type"), max_length=128, db_index=True)
    subject_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    subject_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_type", "subject_id")
    actor = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    metadata = models.JSONField(_("metadata"), default=dict, blank=True, editable=False)

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["subject_type", "subject_id"]),
            models.Index(fields=["organization", "event_type"]),
        ]

    @property
    def occurred_at(self):
        return self.created_at

    def __str__(self):
        return f"{self.event_type} @ #{self.pk}"
