"""Requests: demands entering an organization/service.

A request is independent from any workflow definition. It may *trigger* a
workflow (which then produces a ``WorkflowExecution``); the linkage is by the
generic ``subject`` on ``WorkflowExecution``.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class RequestStatus(models.TextChoices):
    NEW = "NEW", _("New")
    ASSESSING = "ASSESSING", _("Assessing")
    IN_PROGRESS = "IN_PROGRESS", _("In Progress")
    WAITING = "WAITING", _("Waiting")
    RESOLVED = "RESOLVED", _("Resolved")
    CLOSED = "CLOSED", _("Closed")
    CANCELLED = "CANCELLED", _("Cancelled")


class Priority(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")
    URGENT = "URGENT", _("Urgent")
    CRITICAL = "CRITICAL", _("Critical")


class Request(TimeStampedModel):
    """A demand entering the service (new equipment, citizen complaint, …)."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    service = models.ForeignKey(
        "services.Service", on_delete=models.PROTECT, related_name="requests"
    )
    request_type = models.ForeignKey(
        "RequestType", on_delete=models.PROTECT, related_name="requests"
    )
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    requester = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests_requested",
    )
    created_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests_created",
    )
    status = models.CharField(
        _("status"), max_length=16, choices=RequestStatus.choices, default=RequestStatus.NEW
    )
    priority = models.CharField(
        _("priority"), max_length=16, choices=Priority.choices, default=Priority.MEDIUM
    )
    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests_assigned",
    )
    # The workflow definition this request may trigger (optional at create time).
    workflow = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_requests",
    )

    class Meta:
        verbose_name = _("request")
        verbose_name_plural = _("requests")
        indexes = [
            models.Index(fields=["service", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["requester", "status"]),
            models.Index(fields=["priority", "status"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"


class RequestType(TimeStampedModel):
    """Reusable request category (NEW_EQUIPMENT, WASTE_COLLECTION, …)."""

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128)
    description = models.TextField(_("description"), blank=True, default="")
    # Optional: request types may be shared platform-wide or service-specific.
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="request_types",
    )

    class Meta:
        verbose_name = _("request type")
        verbose_name_plural = _("request types")

    def __str__(self):
        return self.name


class RequestComment(TimeStampedModel):
    """Communication/history attached to a request."""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="request_comments",
    )
    body = models.TextField(_("body"))

    class Meta:
        verbose_name = _("request comment")
        verbose_name_plural = _("request comments")
        indexes = [models.Index(fields=["request", "created_at"])]

    def __str__(self):
        return f"Comment on #{self.request_id} by {self.author}"
