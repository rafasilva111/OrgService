"""Services: the operational "micro-organisms" each organization provides.

A service is the anchor for actors, capabilities, resources, workflows,
requests, incidents, problems, events and metrics (see section 2 of the spec).

Each service is classified by a ``ServiceType`` (Finance, HR, Supplies, IT,
Processes, Management) that maps to one of the four ITIL dimensions:

    Organizations and People  — Finance, HR, Management
    Information and Technology — IT
    Partners and Suppliers     — Supplies
    Value Streams and Processes — Processes

A service may also sit under a ``parent`` service (department hierarchy) and
list other services it consumes via ``consumed_services`` (M2M through
``ServiceConsumption``).
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import ActiveModel, SoftDeleteModel, TimeStampedModel

SERVICE_MANAGER_ROLE = "SERVICE_MANAGER"


class ServiceStatus(models.TextChoices):
    OPERATIONAL = "OPERATIONAL", _("Operational")
    DEGRADED = "DEGRADED", _("Degraded")
    DISRUPTED = "DISRUPTED", _("Disrupted")
    PAUSED = "PAUSED", _("Paused")
    RETIRED = "RETIRED", _("Retired")


class ServiceTypeDimension(models.TextChoices):
    ORGANIZATIONS_AND_PEOPLE = "ORGANIZATIONS_AND_PEOPLE", _("Organizations and People")
    INFORMATION_AND_TECHNOLOGY = "INFORMATION_AND_TECHNOLOGY", _("Information and Technology")
    PARTNERS_AND_SUPPLIERS = "PARTNERS_AND_SUPPLIERS", _("Partners and Suppliers")
    VALUE_STREAMS_AND_PROCESSES = "VALUE_STREAMS_AND_PROCESSES", _("Value Streams and Processes")


class ServiceType(models.TextChoices):
    FINANCE = "FINANCE", _("Finance")
    HR = "HR", _("Human Resources")
    SUPPLIES = "SUPPLIES", _("Supplies")
    IT = "IT", _("IT")
    PROCESSES = "PROCESSES", _("Processes")
    MANAGEMENT = "MANAGEMENT", _("Management")


_SERVICE_TYPE_DIMENSION = {
    ServiceType.FINANCE: ServiceTypeDimension.ORGANIZATIONS_AND_PEOPLE,
    ServiceType.HR: ServiceTypeDimension.ORGANIZATIONS_AND_PEOPLE,
    ServiceType.SUPPLIES: ServiceTypeDimension.PARTNERS_AND_SUPPLIERS,
    ServiceType.IT: ServiceTypeDimension.INFORMATION_AND_TECHNOLOGY,
    ServiceType.PROCESSES: ServiceTypeDimension.VALUE_STREAMS_AND_PROCESSES,
    ServiceType.MANAGEMENT: ServiceTypeDimension.ORGANIZATIONS_AND_PEOPLE,
}


class Service(SoftDeleteModel, ActiveModel):
    """A service provided by an organization (e.g. "Waste Management")."""

    _AUTH_ORG_FIELD = "organization"
    _AUTH_SERVICE_FIELD = "id"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="services",
        db_index=True,
    )
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    status = models.CharField(
        _("status"),
        max_length=32,
        choices=ServiceStatus.choices,
        default=ServiceStatus.OPERATIONAL,
        db_index=True,
    )
    service_type = models.ForeignKey(
        "ServiceType",
        on_delete=models.PROTECT,
        related_name="services",
        verbose_name=_("service type"),
        help_text=_("Classification of this service (maps to an ITIL dimension)."),
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        db_index=True,
        help_text=_("Parent service if this is a department/child service."),
    )

    class Meta:
        verbose_name = _("service")
        verbose_name_plural = _("services")
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="u_service_org_name"),
        ]
        indexes = [
            models.Index(fields=["organization", "active"]),
            models.Index(fields=["status", "active"]),
            models.Index(fields=["service_type"]),
        ]

    def __str__(self):
        return f"{self.organization} / {self.name}"

    # ------------------------------------------------------------------ manager
    def manager(self):
        """Active Service Manager actor for this service (or ``None``)."""
        return (
            self.actors.filter(role__code=SERVICE_MANAGER_ROLE, active=True)
            .select_related("role", "person")
            .first()
        )

    @property
    def has_manager(self):
        return self.manager() is not None

    def clean(self):
        """Enforce the platform rule: every service needs a Service Manager."""
        super().clean()
        if self.pk is not None and not self.has_manager:
            raise ValidationError("A service must have an active Service Manager actor.")

    @property
    def dimension(self):
        """The ITIL dimension this service type belongs to."""
        return _SERVICE_TYPE_DIMENSION.get(self.service_type_id)


class ServiceType(models.Model):
    """Classifies a service into one of the four ITIL dimensions."""

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128)
    dimension = models.CharField(
        _("dimension"),
        max_length=64,
        choices=ServiceTypeDimension.choices,
    )

    class Meta:
        verbose_name = _("service type")
        verbose_name_plural = _("service types")

    def __str__(self):
        return self.name


class ServiceCapability(TimeStampedModel):
    """Reusable capability a service may need (e.g. "Route Planning")."""

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128, unique=True)
    description = models.TextField(_("description"), blank=True, default="")
    services = models.ManyToManyField(
        Service, through="ServiceCapabilityAssignment", related_name="capabilities"
    )

    class Meta:
        verbose_name = _("service capability")
        verbose_name_plural = _("service capabilities")

    def __str__(self):
        return self.name


class ServiceCapabilityAssignment(TimeStampedModel):
    """link: which capability a service requires."""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="capability_links")
    capability = models.ForeignKey(
        ServiceCapability, on_delete=models.CASCADE, related_name="service_links"
    )
    required = models.BooleanField(_("required"), default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["service", "capability"], name="u_service_capability"),
        ]
        verbose_name = _("service capability assignment")
        verbose_name_plural = _("service capability assignments")

    def __str__(self):
        return f"{self.service} – {self.capability}"


class ServiceConsumption(TimeStampedModel):
    """A service that another service consumes (Value Streams and Processes).

    Tracks *which* services a given service depends on, with optional
    ``reason`` and ``since`` metadata.
    """

    service = models.ForeignKey(
        Service, on_delete=models.CASCADE, related_name="consumed_through"
    )
    consumed = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="consumed_by",
        help_text=_("The service being consumed."),
    )
    reason = models.TextField(_("reason"), blank=True, default="")
    since = models.DateField(_("since"), null=True, blank=True)

    class Meta:
        verbose_name = _("service consumption")
        verbose_name_plural = _("service consumptions")
        constraints = [
            models.UniqueConstraint(fields=["service", "consumed"], name="u_service_consumption"),
        ]

    def __str__(self):
        return f"{self.service} ← {self.consumed}"


class ActorType(models.TextChoices):
    INTERNAL = "INTERNAL", _("Internal")
    EXTERNAL = "EXTERNAL", _("External")


class ServiceActor(TimeStampedModel, ActiveModel):
    """An actor (role or specific person) tied to a service.

    Internal actors are usually employees; external actors may be contractors,
    partners or suppliers — hence ``person`` is optional and ``actor_type``
    records which kind the assignment represents.
    """

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="actors")
    role = models.ForeignKey("access.Role", on_delete=models.PROTECT, related_name="service_actors")
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="service_actor_links",
        help_text=_("Optional specific person; blank means 'anyone with the role'."),
    )
    actor_type = models.CharField(
        _("actor type"), max_length=16, choices=ActorType.choices, default=ActorType.INTERNAL
    )
    responsibility = models.CharField(_("responsibility"), max_length=255, blank=True, default="")

    class Meta:
        verbose_name = _("service actor")
        verbose_name_plural = _("service actors")
        constraints = [
            models.UniqueConstraint(
                fields=["service", "role", "person"],
                condition=models.Q(person__isnull=False),
                name="u_service_actor_person",
            ),
            models.UniqueConstraint(
                fields=["service", "role"],
                condition=models.Q(person__isnull=True),
                name="u_service_actor_role",
            ),
        ]

    def __str__(self):
        actor = self.person or self.role
        return f"{self.service} – {actor} ({self.actor_type})"
