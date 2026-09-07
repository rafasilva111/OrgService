"""Access control: reusable roles and atomic permissions.

Deliberately NOT the Django ``auth.Group``/``Permission`` mechanism:
  * permissions are our own atoms, expressed as codes like ``"request.view"``;
  * roles are reusable and not owned by any organization;
  * grants are scoped through ``OrganizationMembership`` + ``ServiceAccess``
    (see ``apps.common.authorization``).
"""

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
