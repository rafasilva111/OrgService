"""Incidents: unexpected disruptions to a service.

Distinction kept explicit with ``apps.problems``:
  * Incident = something is wrong NOW;
  * Problem = WHY does this keep happening? (root cause of several incidents).
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel
from apps.requests.models import Priority


class IncidentStatus(models.TextChoices):
    OPEN = "OPEN", _("Open")
    INVESTIGATING = "INVESTIGATING", _("Investigating")
    RESOLVED = "RESOLVED", _("Resolved")
    CLOSED = "CLOSED", _("Closed")


class Impact(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")
    MAJOR = "MAJOR", _("Major")


class Urgency(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")
    IMMEDIATE = "IMMEDIATE", _("Immediate")


class Incident(TimeStampedModel):
    """An unexpected disruption (breakdown, failed collection, outage, …)."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    service = models.ForeignKey(
        "services.Service", on_delete=models.PROTECT, related_name="incidents"
    )
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    reported_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents_reported",
    )
    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents_assigned",
    )
    priority = models.CharField(
        _("priority"), max_length=16, choices=Priority.choices, default=Priority.MEDIUM
    )
    impact = models.CharField(
        _("impact"), max_length=16, choices=Impact.choices, default=Impact.MEDIUM
    )
    urgency = models.CharField(
        _("urgency"), max_length=16, choices=Urgency.choices, default=Urgency.LOW
    )
    status = models.CharField(
        _("status"), max_length=16, choices=IncidentStatus.choices, default=IncidentStatus.OPEN
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)

    class Meta:
        verbose_name = _("incident")
        verbose_name_plural = _("incidents")
        indexes = [
            models.Index(fields=["service", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["priority", "urgency", "status"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"

    @property
    def related_request(self):
        """Execution subject lookup convenience (kept as a property, DB-safe)."""
        return None  # intentionally None; enrich in a later phase


class IncidentUpdate(TimeStampedModel):
    """Progress/history entries on an incident."""

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="updates")
    author = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incident_updates",
    )
    message = models.TextField(_("message"))
    status = models.CharField(
        _("status"), max_length=16, choices=IncidentStatus.choices, blank=True
    )

    class Meta:
        verbose_name = _("incident update")
        verbose_name_plural = _("incident updates")
        indexes = [models.Index(fields=["incident", "created_at"])]

    def __str__(self):
        return f"Update on #{self.incident_id} by {self.author}"
