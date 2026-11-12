"""Service Offerings: definitions (blueprint) vs executions (instances).

Versioning: a ``ServiceOffering`` row is a *version* of an offering; the logical
offering is ``(service, name)`` and ``version`` is a positive integer. New
definitions are added as new rows, never by mutating a released version.

The subject that starts an execution is intentionally generic
(``GenericForeignKey``) so requests, incidents, changes and manual triggers
can all be wired without import cycles.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class OfferingStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    ACTIVE = "ACTIVE", _("Active")
    DEPRECATED = "DEPRECATED", _("Deprecated")


class OfferingTrigger(models.TextChoices):
    MANUAL = "MANUAL", _("Manual")
    REQUEST = "REQUEST", _("Request")
    EVENT = "EVENT", _("Event")


class ServiceOfferingType(TimeStampedModel):
    """Platform-wide category of service offerings (Manutenção, Reclamação, …).

    Replaces the former ``requests.RequestType``: a request's type is now
    derived from the offering it asks for (``request.offering.offering_type``).
    """

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128)
    description = models.TextField(_("description"), blank=True, default="")

    class Meta:
        verbose_name = _("service offering type")
        verbose_name_plural = _("service offering types")
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServiceOffering(TimeStampedModel):
    """A versioned definition of how work is performed for a service."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    service = models.ForeignKey(
        "services.Service", on_delete=models.CASCADE, related_name="offerings"
    )
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    version = models.PositiveIntegerField(_("version"), default=1)
    status = models.CharField(
        _("status"), max_length=32, choices=OfferingStatus.choices, default=OfferingStatus.DRAFT
    )
    trigger = models.CharField(
        _("trigger"), max_length=32, choices=OfferingTrigger.choices, default=OfferingTrigger.MANUAL
    )
    offering_type = models.ForeignKey(
        ServiceOfferingType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="offerings",
        verbose_name=_("type"),
    )
    created_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_offerings",
    )

    class Meta:
        verbose_name = _("service offering")
        verbose_name_plural = _("service offerings")
        constraints = [
            models.UniqueConstraint(
                fields=["service", "name", "version"], name="u_offering_version"
            ),
        ]
        indexes = [
            models.Index(fields=["service", "status"]),
            models.Index(fields=["status", "trigger"]),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.service})"

    @classmethod
    def current_version(cls, service, name):
        """Highest published version of a logical offering."""
        return (
            cls.objects.filter(service=service, name=name)
            .exclude(status=OfferingStatus.DRAFT)
            .order_by("-version")
            .first()
        )


class StepType(models.TextChoices):
    START = "START", _("Start")
    TASK = "TASK", _("Task")
    GATEWAY = "GATEWAY", _("Gateway")
    APPROVAL = "APPROVAL", _("Approval")
    END = "END", _("End")


class OfferingStep(TimeStampedModel):
    """An activity/decision point inside an offering definition."""

    offering = models.ForeignKey(ServiceOffering, on_delete=models.CASCADE, related_name="steps")
    code = models.SlugField(_("code"), max_length=64)
    name = models.CharField(_("name"), max_length=255)
    step_type = models.CharField(
        _("step type"), max_length=16, choices=StepType.choices, default=StepType.TASK
    )
    order = models.PositiveIntegerField(_("order"), default=0)
    responsible_role = models.ForeignKey(
        "access.Role",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offering_steps",
        help_text=_("Role that performs this step."),
    )
    # Offering accountable for the step; its service is
    # ``service_offering.service``. Blank means the step's own offering, so set
    # it only to hand the step to another offering (usually of another service).
    service_offering = models.ForeignKey(
        ServiceOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="responsible_steps",
        verbose_name=_("responsible service offering"),
    )
    sla_minutes = models.PositiveIntegerField(
        _("SLA (minutes)"), null=True, blank=True, help_text=_("Deadline for this step.")
    )
    required_inputs = models.JSONField(_("required inputs"), default=list, blank=True)
    outputs = models.JSONField(_("outputs"), default=list, blank=True)

    class Meta:
        verbose_name = _("offering step")
        verbose_name_plural = _("offering steps")
        constraints = [
            models.UniqueConstraint(fields=["offering", "code"], name="u_offering_step_code"),
            models.UniqueConstraint(fields=["offering", "order"], name="u_offering_step_order"),
        ]

    def __str__(self):
        return f"{self.offering} · {self.order}: {self.name}"

    @property
    def effective_offering(self):
        """Offering accountable for this step (defaults to the step's own offering)."""
        return self.service_offering or self.offering

    @property
    def effective_service(self):
        return self.effective_offering.service

    @property
    def is_delegated(self):
        return self.service_offering_id is not None and self.service_offering_id != self.offering_id


class OfferingTransition(TimeStampedModel):
    """Directed movement between steps, supporting branching via conditions."""

    offering = models.ForeignKey(
        ServiceOffering, on_delete=models.CASCADE, related_name="transitions"
    )
    from_step = models.ForeignKey(OfferingStep, on_delete=models.CASCADE, related_name="outgoing")
    to_step = models.ForeignKey(OfferingStep, on_delete=models.CASCADE, related_name="incoming")
    name = models.CharField(_("name"), max_length=255, blank=True, default="")
    order = models.PositiveIntegerField(_("order"), default=0)
    # "condition A" / "condition B" — JSON expression interpreted later.
    condition = models.JSONField(_("condition"), null=True, blank=True)

    class Meta:
        verbose_name = _("offering transition")
        verbose_name_plural = _("offering transitions")
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "from_step", "order"], name="u_transition_order"
            ),
        ]

    def __str__(self):
        return f"{self.from_step} → {self.to_step}"


class ExecutionStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    RUNNING = "RUNNING", _("Running")
    WAITING = "WAITING", _("Waiting")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")
    CANCELLED = "CANCELLED", _("Cancelled")


class OfferingExecution(TimeStampedModel):
    """An actual run of an offering definition."""

    _AUTH_ORG_FIELD = None
    _AUTH_SERVICE_FIELD = "offering__service"

    offering = models.ForeignKey(
        ServiceOffering, on_delete=models.PROTECT, related_name="executions"
    )
    status = models.CharField(
        _("status"), max_length=16, choices=ExecutionStatus.choices, default=ExecutionStatus.PENDING
    )
    current_step = models.ForeignKey(
        OfferingStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_executions",
    )
    # Subject that started this execution (Request, Incident, Change, …).
    subject_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    subject_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_type", "subject_id")

    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    created_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offering_executions_created",
    )

    class Meta:
        verbose_name = _("offering execution")
        verbose_name_plural = _("offering executions")
        indexes = [
            models.Index(fields=["offering", "status"]),
            models.Index(fields=["status", "started_at"]),
        ]

    def __str__(self):
        return f"Execution #{self.pk} ({self.offering} – {self.status})"


class TaskStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ASSIGNED = "ASSIGNED", _("Assigned")
    IN_PROGRESS = "IN_PROGRESS", _("In Progress")
    DONE = "DONE", _("Done")
    BLOCKED = "BLOCKED", _("Blocked")
    CANCELLED = "CANCELLED", _("Cancelled")


class OfferingTask(TimeStampedModel):
    """Executable work item generated by an offering execution."""

    execution = models.ForeignKey(OfferingExecution, on_delete=models.CASCADE, related_name="tasks")
    step = models.ForeignKey(OfferingStep, on_delete=models.PROTECT, related_name="execution_tasks")
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offering_tasks",
    )
    status = models.CharField(
        _("status"), max_length=16, choices=TaskStatus.choices, default=TaskStatus.PENDING
    )
    deadline = models.DateTimeField(_("deadline"), null=True, blank=True)
    result = models.JSONField(_("result"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("offering task")
        verbose_name_plural = _("offering tasks")
        indexes = [
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["execution", "status"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"
