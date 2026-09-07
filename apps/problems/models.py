"""Problems: the underlying causes behind recurring incidents.

Incident  = "something is wrong now"
Problem   = "why does this keep happening?"
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel
from apps.requests.models import Priority


class ProblemStatus(models.TextChoices):
    OPEN = "OPEN", _("Open")
    DIAGNOSED = "DIAGNOSED", _("Diagnosed")
    IN_PROGRESS = "IN_PROGRESS", _("In Progress")
    RESOLVED = "RESOLVED", _("Resolved")
    CLOSED = "CLOSED", _("Closed")


class Problem(TimeStampedModel):
    """Root cause of one or more incidents."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    service = models.ForeignKey(
        "services.Service", on_delete=models.PROTECT, related_name="problems"
    )
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    status = models.CharField(
        _("status"), max_length=16, choices=ProblemStatus.choices, default=ProblemStatus.OPEN
    )
    priority = models.CharField(
        _("priority"), max_length=16, choices=Priority.choices, default=Priority.MEDIUM
    )
    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="problems_assigned",
    )
    root_cause = models.TextField(_("root cause"), blank=True, default="")

    class Meta:
        verbose_name = _("problem")
        verbose_name_plural = _("problems")
        indexes = [
            models.Index(fields=["service", "status"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"


class ProblemIncident(TimeStampedModel):
    """Association: which incidents share this problem."""

    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="incident_links")
    incident = models.ForeignKey(
        "incidents.Incident", on_delete=models.CASCADE, related_name="problem_links"
    )

    class Meta:
        verbose_name = _("problem incident")
        verbose_name_plural = _("problem incidents")
        constraints = [
            models.UniqueConstraint(fields=["problem", "incident"], name="u_problem_incident"),
        ]

    def __str__(self):
        return f"Problem #{self.problem_id} ← Incident #{self.incident_id}"
