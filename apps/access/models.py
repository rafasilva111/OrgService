"""Access control: reusable roles and atomic permissions.

Deliberately NOT the Django ``auth.Group``/``Permission`` mechanism:
  * permissions are our own atoms, expressed as codes like ``"request.view"``;
  * roles are reusable and not owned by any organization;
  * grants are scoped through ``OrganizationMembership`` + ``ServiceAccess``
    (see ``apps.common.authorization``).
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import ActiveModel, SoftDeleteModel, TimeStampedModel


class Permission(TimeStampedModel):
    """An atomic action a role may perform.

    Convention for codes: ``<module>.<verb>``, e.g. ``request.view``,
    ``incident.resolve``. Read and write verbs are distinct atoms so that
    visibility is never implicitly upgraded to mutability.
    """

    code = models.SlugField(_("code"), unique=True, max_length=64)
    label = models.CharField(_("label"), max_length=128)
    description = models.TextField(_("description"), blank=True, default="")
    module = models.CharField(
        _("module"),
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text=_("Domain the permission belongs to (e.g. ``requests``)."),
    )

    class Meta:
        verbose_name = _("permission")
        verbose_name_plural = _("permissions")
        ordering = ("module", "code")

    def __str__(self):
        return self.code


class Role(SoftDeleteModel, ActiveModel):
    """Reusable role definition (EXECUTIVE, SERVICE_MANAGER, OPERATOR, …).

    Not tied to a single organization — memberships bind roles to scopes.
    """

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128)
    description = models.TextField(_("description"), blank=True, default="")
    is_system = models.BooleanField(
        _("system role"),
        default=False,
        help_text=_("Seeded by the platform; protects against deletion."),
    )
    permissions = models.ManyToManyField(Permission, related_name="roles", through="RolePermission")

    class Meta:
        verbose_name = _("role")
        verbose_name_plural = _("roles")
        ordering = ("code",)

    def __str__(self):
        return self.name


class RolePermission(TimeStampedModel):
    """Grant of a permission to a role (reversible)."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="rolepermissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="rolepermissions"
    )
    active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("role permission")
        verbose_name_plural = _("role permissions")
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="u_role_permission"),
        ]

    def __str__(self):
        return f"{self.role}:{self.permission}"


class GrantScopeType(models.TextChoices):
    """How a ``RolePermission`` is narrowed for a membership."""

    INHERIT = "INHERIT", _("Inherit membership scope")
    ALL_SERVICES = "ALL_SERVICES", _("All services")
    BELOW_SERVICES = "BELOW_SERVICES", _("Below a service or organization")
    SPECIFIC_SERVICE = "SPECIFIC_SERVICE", _("Specific service")
    ORG_SUBTREE = "ORG_SUBTREE", _("Membership organization subtree")


class GrantScope(TimeStampedModel, ActiveModel):
    """Optional scope attached to a single role permission.

    No scope rows means ``INHERIT``: the membership's organization subtree (and
    any ``ServiceAccess`` rows) decides the reach. An explicit scope overrides
    that default for the permission it belongs to.
    """

    role_permission = models.ForeignKey(
        RolePermission, on_delete=models.CASCADE, related_name="grant_scopes"
    )
    scope_type = models.CharField(
        _("scope type"),
        max_length=32,
        choices=GrantScopeType.choices,
        default=GrantScopeType.INHERIT,
        db_index=True,
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="grant_scopes",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="grant_scopes",
    )

    class Meta:
        verbose_name = _("grant scope")
        verbose_name_plural = _("grant scopes")
        constraints = [
            models.UniqueConstraint(
                fields=["role_permission", "scope_type", "service", "organization"],
                name="u_grant_scope",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["role_permission", "active"]),
            models.Index(fields=["scope_type", "service"]),
        ]

    def __str__(self):
        target = self.service or self.organization or "membership"
        return f"{self.role_permission} → {self.get_scope_type_display()} ({target})"

    def clean(self):
        super().clean()
        errors = {}
        if self.scope_type == GrantScopeType.SPECIFIC_SERVICE:
            if self.service_id is None:
                errors["service"] = _("A specific-service scope requires a service.")
            if self.organization_id is not None:
                errors["organization"] = _("A specific-service scope cannot have an organization.")
        elif self.scope_type == GrantScopeType.BELOW_SERVICES:
            if (self.service_id is None) == (self.organization_id is None):
                errors["service"] = _(
                    "A below-services scope requires exactly one service or organization."
                )
        elif self.scope_type in (GrantScopeType.ALL_SERVICES, GrantScopeType.ORG_SUBTREE):
            if self.service_id is not None or self.organization_id is not None:
                errors["service"] = _("This scope type cannot have a service or organization.")
        if errors:
            raise ValidationError(errors)


class ServiceAccess(TimeStampedModel, ActiveModel):
    """Restricts a membership to a subset of services.

    Semantics (see ``apps.common.authorization``): a membership WITH
    ``ServiceAccess`` rows can only reach those services; a membership WITHOUT
    any row reaches every service under its organization subtree.
    """

    membership = models.ForeignKey(
        "organizations.OrganizationMembership",
        on_delete=models.CASCADE,
        related_name="service_access",
    )
    service = models.ForeignKey(
        "services.Service", on_delete=models.CASCADE, related_name="service_access"
    )

    class Meta:
        verbose_name = _("service access")
        verbose_name_plural = _("service accesses")
        constraints = [
            models.UniqueConstraint(fields=["membership", "service"], name="u_membership_service"),
        ]

    def __str__(self):
        return f"{self.membership} → {self.service}"
