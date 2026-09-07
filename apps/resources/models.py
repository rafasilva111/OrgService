"""Resources: generic abstraction over vehicles, equipment, materials, facilities.

Class hierarchy (multi-table inheritance keeps every subtype a real
``Resource`` so generic assignments/visibility keep working):
    Resource (generic)
      ├── Asset            (persistent, identifiable — e.g. vehicle RS-023)
      │     └── Equipment  (specialized asset — container, compactor, …)
      └── Material         (consumable — bags, gloves, fuel, …)

``ResourceAssignment`` points at any assignable target (service, person,
workflow task, organization,…) through a ``GenericForeignKey`` to avoid import
cycles between apps.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import ActiveModel, SoftDeleteModel, TimeStampedModel


class ResourceType(models.TextChoices):
    VEHICLE = "VEHICLE", _("Vehicle")
    EQUIPMENT = "EQUIPMENT", _("Equipment")
    MATERIAL = "MATERIAL", _("Material")
    FACILITY = "FACILITY", _("Facility")
    SUPPLIER = "SUPPLIER", _("Supplier")


class Resource(SoftDeleteModel, ActiveModel):
    """Generic resource abstraction."""

    _AUTH_ORG_FIELD = "organization"
    _AUTH_SERVICE_FIELD = None

    resource_type = models.CharField(
        _("resource type"),
        max_length=16,
        choices=ResourceType.choices,
        db_index=True,
    )
    name = models.CharField(_("name"), max_length=255)
    code = models.SlugField(_("code"), max_length=64, null=True, blank=True)
    description = models.TextField(_("description"), blank=True, default="")
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="resources",
        null=True,
        blank=True,
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resources",
    )

    class Meta:
        verbose_name = _("resource")
        verbose_name_plural = _("resources")
        indexes = [
            models.Index(fields=["organization", "resource_type"]),
            models.Index(fields=["resource_type", "active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.resource_type})"


class AssetStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", _("Available")
    ASSIGNED = "ASSIGNED", _("Assigned")
    MAINTENANCE = "MAINTENANCE", _("In Maintenance")
    RETIRED = "RETIRED", _("Retired")


class Asset(Resource):
    """A persistent, identifiable resource (e.g. vehicle plate RS-023)."""

    serial_number = models.CharField(
        _("serial number"), max_length=64, null=True, blank=True, db_index=True
    )
    purchase_date = models.DateField(_("purchase date"), null=True, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=AssetStatus.choices, default=AssetStatus.AVAILABLE
    )

    class Meta:
        verbose_name = _("asset")
        verbose_name_plural = _("assets")

    def __str__(self):
        ident = self.serial_number or self.code or self.pk
        return f"{self.name} ({ident})"


class Equipment(Asset):
    """Specialized equipment (container, compactor, protective gear, …)."""

    equipment_type = models.CharField(_("equipment type"), max_length=128, blank=True, default="")
    capacity = models.CharField(_("capacity"), max_length=64, blank=True, default="")

    class Meta:
        verbose_name = _("equipment")
        verbose_name_plural = _("equipment")

    def __str__(self):
        return f"Equipment: {super().__str__()}"


class Material(Resource):
    """Consumable resource tracked by stock level."""

    quantity_on_hand = models.DecimalField(
        _("quantity on hand"), max_digits=14, decimal_places=2, default=0
    )
    unit = models.CharField(_("unit"), max_length=32, blank=True, default="")
    reorder_level = models.DecimalField(
        _("reorder level"), max_digits=14, decimal_places=2, null=True, blank=True
    )

    class Meta:
        verbose_name = _("material")
        verbose_name_plural = _("materials")

    def __str__(self):
        return f"Material: {self.name} ({self.quantity_on_hand} {self.unit})"


class ResourceAssignment(TimeStampedModel):
    """Allocation of a resource to a service/person/workflow/task/org…"""

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="assignments")
    # Assignable target — generic to avoid coupling resource to every domain app.
    target_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    target_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("target_type", "target_id")
    # Optional role of the assignment (driver, responsible, backup…).
    label = models.CharField(_("label"), max_length=128, blank=True, default="")
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    ended_at = models.DateTimeField(_("ended at"), null=True, blank=True)

    class Meta:
        verbose_name = _("resource assignment")
        verbose_name_plural = _("resource assignments")

        indexes = [
            models.Index(fields=["resource", "ended_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.resource} → {self.target}"
