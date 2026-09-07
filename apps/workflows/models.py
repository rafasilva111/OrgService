"""Workflows: definitions (blueprint) vs executions (instances).

Versioning: a ``Workflow`` row is a *version* of a workflow; the logical
workflow is ``(service, name)`` and ``version`` is a positive integer. New
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


class WorkflowStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    ACTIVE = "ACTIVE", _("Active")
    DEPRECATED = "DEPRECATED", _("Deprecated")


class WorkflowTrigger(models.TextChoices):
    MANUAL = "MANUAL", _("Manual")
    REQUEST = "REQUEST", _("Request")
    EVENT = "EVENT", _("Event")


class Workflow(TimeStampedModel):
    """A versioned definition of how work is performed for a service."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    service = models.ForeignKey(
        "services.Service", on_delete=models.CASCADE, related_name="workflows"
    )
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    version = models.PositiveIntegerField(_("version"), default=1)
    status = models.CharField(
        _("status"), max_length=32, choices=WorkflowStatus.choices, default=WorkflowStatus.DRAFT
    )
    trigger = models.CharField(
        _("trigger"), max_length=32, choices=WorkflowTrigger.choices, default=WorkflowTrigger.MANUAL
    )
    created_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workflows",
    )

    class Meta:
        verbose_name = _("workflow")
        verbose_name_plural = _("workflows")
        constraints = [
            models.UniqueConstraint(
                fields=["service", "name", "version"], name="u_workflow_version"
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
        """Highest published version of a logical workflow."""
        return (
            cls.objects.filter(service=service, name=name)
            .exclude(status=WorkflowStatus.DRAFT)
            .order_by("-version")
            .first()
        )


class StepType(models.TextChoices):
    START = "START", _("Start")
    TASK = "TASK", _("Task")
    GATEWAY = "GATEWAY", _("Gateway")
    APPROVAL = "APPROVAL", _("Approval")
    END = "END", _("End")


class WorkflowStep(TimeStampedModel):
    """An activity/decision point inside a workflow definition."""

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="steps")
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
        related_name="workflow_steps",
        help_text=_("Role that performs this step."),
    )
    sla_minutes = models.PositiveIntegerField(
        _("SLA (minutes)"), null=True, blank=True, help_text=_("Deadline for this step.")
    )
    required_inputs = models.JSONField(_("required inputs"), default=list, blank=True)
    outputs = models.JSONField(_("outputs"), default=list, blank=True)

    class Meta:
        verbose_name = _("workflow step")
        verbose_name_plural = _("workflow steps")
        constraints = [
            models.UniqueConstraint(fields=["workflow", "code"], name="u_workflow_step_code"),
            models.UniqueConstraint(fields=["workflow", "order"], name="u_workflow_step_order"),
        ]

    def __str__(self):
        return f"{self.workflow} · {self.order}: {self.name}"


class WorkflowTransition(TimeStampedModel):
    """Directed movement between steps, supporting branching via conditions."""

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="transitions")
    from_step = models.ForeignKey(WorkflowStep, on_delete=models.CASCADE, related_name="outgoing")
    to_step = models.ForeignKey(WorkflowStep, on_delete=models.CASCADE, related_name="incoming")
    name = models.CharField(_("name"), max_length=255, blank=True, default="")
    order = models.PositiveIntegerField(_("order"), default=0)
    # "condition A" / "condition B" — JSON expression interpreted later.
    condition = models.JSONField(_("condition"), null=True, blank=True)

    class Meta:
        verbose_name = _("workflow transition")
        verbose_name_plural = _("workflow transitions")
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "from_step", "order"], name="u_transition_order"
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


class WorkflowExecution(TimeStampedModel):
    """An actual run of a workflow definition."""

    _AUTH_ORG_FIELD = None
    _AUTH_SERVICE_FIELD = "workflow__service"

    workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name="executions")
    status = models.CharField(
        _("status"), max_length=16, choices=ExecutionStatus.choices, default=ExecutionStatus.PENDING
    )
    current_step = models.ForeignKey(
        WorkflowStep,
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
        related_name="workflow_executions_created",
    )

    class Meta:
        verbose_name = _("workflow execution")
        verbose_name_plural = _("workflow executions")
        indexes = [
            models.Index(fields=["workflow", "status"]),
            models.Index(fields=["status", "started_at"]),
        ]

    def __str__(self):
        return f"Execution #{self.pk} ({self.workflow} – {self.status})"


class TaskStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ASSIGNED = "ASSIGNED", _("Assigned")
    IN_PROGRESS = "IN_PROGRESS", _("In Progress")
    DONE = "DONE", _("Done")
    BLOCKED = "BLOCKED", _("Blocked")
    CANCELLED = "CANCELLED", _("Cancelled")


class WorkflowTask(TimeStampedModel):
    """Executable work item generated by a workflow execution."""

    execution = models.ForeignKey(WorkflowExecution, on_delete=models.CASCADE, related_name="tasks")
    step = models.ForeignKey(WorkflowStep, on_delete=models.PROTECT, related_name="execution_tasks")
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_tasks",
    )
    status = models.CharField(
        _("status"), max_length=16, choices=TaskStatus.choices, default=TaskStatus.PENDING
    )
    deadline = models.DateTimeField(_("deadline"), null=True, blank=True)
    result = models.JSONField(_("result"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("workflow task")
        verbose_name_plural = _("workflow tasks")
        indexes = [
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["execution", "status"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"
