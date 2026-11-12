"""Service Offerings: definitions (blueprint) vs executions (instances).

Versioning: a ``ServiceOffering`` row is a *version* of an offering; the logical
offering is ``(service, name)`` and ``version`` is a positive integer. New
definitions are added as new rows, never by mutating a released version.

The subject that starts an execution is intentionally generic
(``GenericForeignKey``) so requests, incidents, changes and scheduled runs
can all be wired without import cycles.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import Max
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class OfferingStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    ACTIVE = "ACTIVE", _("Active")
    DEPRECATED = "DEPRECATED", _("Deprecated")


class RecurrenceFrequency(models.TextChoices):
    DAILY = "DAILY", _("Daily")
    WEEKLY = "WEEKLY", _("Weekly")
    MONTHLY = "MONTHLY", _("Monthly")
    YEARLY = "YEARLY", _("Yearly")


RECURRENCE_FIELDS = (
    "recurrence_frequency",
    "recurrence_interval",
    "recurrence_weekdays",
    "recurrence_month_day",
    "recurrence_month",
    "recurrence_start",
    "recurrence_end",
)


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
    # How the offering is started — independent flags, at least one is set:
    # citizens can ask for it (Novo Pedido) and/or it runs on its recurrence.
    on_request = models.BooleanField(_("on request"), default=True)
    on_schedule = models.BooleanField(_("recurring"), default=False)
    # Total SLA: sum of the step SLAs along the normal path Início → Fim
    # (error branches excluded). Computed when the plan is saved; steps handed
    # to another service take that offering's total (see builder.offering_sla).
    sla_minutes = models.PositiveIntegerField(_("SLA (minutes)"), null=True, blank=True)
    # Recurrence (only when ``on_schedule``): "every <interval> <frequency>" on
    # the given weekdays (0 = Monday) / day of month / month, from start to end.
    recurrence_frequency = models.CharField(
        _("repeats"), max_length=16, choices=RecurrenceFrequency.choices, blank=True, default=""
    )
    recurrence_interval = models.PositiveSmallIntegerField(_("every"), default=1)
    recurrence_weekdays = models.JSONField(_("weekdays"), default=list, blank=True)
    recurrence_month_day = models.PositiveSmallIntegerField(
        _("day of month"), null=True, blank=True
    )
    recurrence_month = models.PositiveSmallIntegerField(_("month"), null=True, blank=True)
    recurrence_start = models.DateField(_("starts on"), null=True, blank=True)
    recurrence_end = models.DateField(_("ends on"), null=True, blank=True)
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
            models.CheckConstraint(
                condition=models.Q(on_request=True) | models.Q(on_schedule=True),
                name="offering_on_request_or_schedule",
            ),
        ]
        indexes = [
            models.Index(fields=["service", "status"]),
            models.Index(fields=["status", "on_request"]),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.service})"

    # ------------------------------------------------------------ recurrence
    @property
    def is_recurring(self):
        return self.on_schedule and bool(self.recurrence_frequency)

    def recurrence_summary(self):
        """Human sentence, e.g. "Every month on day 1, starting 01/12/2026"."""
        from apps.offerings.recurrence import summary

        return summary(self) if self.is_recurring else ""

    def availability_summary(self):
        """E.g. "On request · Every month on day 1, starting 01/12/2026"."""
        parts = []
        if self.on_request:
            parts.append(str(_("On request")))
        if self.is_recurring:
            parts.append(self.recurrence_summary())
        return " · ".join(parts)

    def next_occurrences(self, count=3, after=None):
        from apps.offerings.recurrence import occurrences

        return occurrences(self, count=count, after=after) if self.is_recurring else []

    @property
    def is_editable(self):
        """Only drafts can change; published versions are immutable (ADR-005)."""
        return self.status == OfferingStatus.DRAFT

    def publish(self):
        """Validate the step graph, make this version ACTIVE and deprecate the
        previously active versions of the same ``(service, name)``."""
        from apps.offerings.builder import sync_offering_sla, validate_offering_graph

        validate_offering_graph(self)
        with transaction.atomic():
            previous = list(
                ServiceOffering.objects.filter(
                    service_id=self.service_id, name=self.name, status=OfferingStatus.ACTIVE
                ).exclude(pk=self.pk)
            )
            for old in previous:
                old.status = OfferingStatus.DEPRECATED
                old.save(update_fields=["status", "updated_at"])
                sync_offering_sla(old)  # its SLA becomes inactive
            self.status = OfferingStatus.ACTIVE
            self.save(update_fields=["status", "updated_at"])
            sync_offering_sla(self)

    def new_version(self, created_by=None):
        """Copy this offering (steps and transitions included) into a DRAFT with
        the next version number."""
        with transaction.atomic():
            latest = ServiceOffering.objects.filter(
                service_id=self.service_id, name=self.name
            ).aggregate(v=Max("version"))["v"]
            draft = ServiceOffering.objects.create(
                service_id=self.service_id,
                name=self.name,
                description=self.description,
                version=(latest or self.version) + 1,
                status=OfferingStatus.DRAFT,
                on_request=self.on_request,
                on_schedule=self.on_schedule,
                sla_minutes=self.sla_minutes,
                **{name: getattr(self, name) for name in RECURRENCE_FIELDS},
                offering_type_id=self.offering_type_id,
                created_by=created_by,
            )
            copies = {}
            for step in self.steps.order_by("order"):
                old_pk = step.pk
                step.pk = None
                step.id = None
                step.offering = draft
                step.save()
                copies[old_pk] = step
            for tr in self.transitions.all():
                OfferingTransition.objects.create(
                    offering=draft,
                    from_step=copies[tr.from_step_id],
                    to_step=copies[tr.to_step_id],
                    outcome=tr.outcome,
                    order=tr.order,
                    name=tr.name,
                    condition=tr.condition,
                )
            from apps.offerings.builder import sync_offering_sla

            sync_offering_sla(draft)
        return draft

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
    NOTIFY = "NOTIFY", _("Notification")
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
    # Notification steps: text sent to the requester (declarative until an
    # execution engine exists).
    message = models.TextField(_("message"), blank=True, default="")
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
    def is_external(self):
        """Handed to an offering of *another* service: its type and SLA come
        from that offering and it has no role of its own."""
        return (
            self.service_offering_id is not None
            and self.service_offering.service_id != self.offering.service_id
        )

    @property
    def is_delegated(self):
        return self.service_offering_id is not None and self.service_offering_id != self.offering_id

    def _target(self, outcome):
        # Iterates ``outgoing`` so a ``prefetch_related("outgoing__to_step")``
        # avoids per-step queries.
        for transition in self.outgoing.all():
            if transition.outcome == outcome:
                return transition.to_step
        return None

    @property
    def next_step(self):
        return self._target(TransitionOutcome.SUCCESS)

    @property
    def failure_step(self):
        return self._target(TransitionOutcome.FAILURE)


class TransitionOutcome(models.TextChoices):
    SUCCESS = "SUCCESS", _("Success")
    FAILURE = "FAILURE", _("Error")


class OfferingTransition(TimeStampedModel):
    """Directed movement between steps, supporting branching via conditions."""

    offering = models.ForeignKey(
        ServiceOffering, on_delete=models.CASCADE, related_name="transitions"
    )
    from_step = models.ForeignKey(OfferingStep, on_delete=models.CASCADE, related_name="outgoing")
    to_step = models.ForeignKey(OfferingStep, on_delete=models.CASCADE, related_name="incoming")
    name = models.CharField(_("name"), max_length=255, blank=True, default="")
    order = models.PositiveIntegerField(_("order"), default=0)
    # Branching: the SUCCESS edge is the normal path, FAILURE the "on error"
    # path (e.g. notify the requester). At most one of each per step.
    outcome = models.CharField(
        _("outcome"),
        max_length=16,
        choices=TransitionOutcome.choices,
        default=TransitionOutcome.SUCCESS,
    )
    # "condition A" / "condition B" — JSON expression interpreted later.
    condition = models.JSONField(_("condition"), null=True, blank=True)

    class Meta:
        verbose_name = _("offering transition")
        verbose_name_plural = _("offering transitions")
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "from_step", "outcome"], name="u_transition_outcome"
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
