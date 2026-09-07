"""Organizations: hierarchical organizational units + memberships.

Hierarchy is stored twice on purpose:
  * ``parent`` FK — friendly traversal / form handling;
  * ``path`` (PostgreSQL ``ltree``) — indexed materialized path for fast
    ancestor/descendant queries (``path @> x`` / ``path <@ x``).

Use the ltree lookups ``path__descendants``, ``path__ancestors``,
``path__nlevel`` to query whole subtrees in a single DB call.
"""

from django.contrib.postgres.indexes import GistIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.fields import LTreeField
from apps.common.models import ActiveModel, SoftDeleteModel, TimeStampedModel


class OrganizationType(models.TextChoices):
    COUNTRY = "COUNTRY", _("Country")
    DISTRICT = "DISTRICT", _("District")
    MUNICIPALITY = "MUNICIPALITY", _("Municipality")
    PARISH = "PARISH", _("Parish")
    DEPARTMENT = "DEPARTMENT", _("Department")
    OTHER = "OTHER", _("Other")


class Organization(SoftDeleteModel, ActiveModel):
    """An organizational unit in the hierarchy (District → Municipality → Parish…).

    ``code`` is the stable, URL-safe label used inside the ltree ``path``.
    It is unique among the children of a single parent.
    """

    _AUTH_ORG_FIELD = "id"
    _AUTH_SERVICE_FIELD = None

    name = models.CharField(_("name"), max_length=255)
    code = models.SlugField(_("code"), max_length=64)
    description = models.TextField(_("description"), blank=True, default="")
    organization_type = models.CharField(
        _("organization type"),
        max_length=32,
        choices=OrganizationType.choices,
        default=OrganizationType.OTHER,
        db_index=True,
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        db_index=True,
        help_text=_("Parent organizational unit. Null for the root."),
    )
    # PostgreSQL ltree materialized path, e.g. "district.municipality.parish".
    path = LTreeField(_("path"), null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("organization")
        verbose_name_plural = _("organizations")
        ordering = ("path",)
        constraints = [
            models.UniqueConstraint(fields=["parent", "code"], name="u_organization_parent_code"),
        ]
        indexes = [
            GistIndex(name="org_path_gin_idx", fields=["path"], opclasses=["gist_ltree_ops"]),
        ]

    def __str__(self):
        return self.path or f"{self.code} ({self.name})"

    def save(self, *args, **kwargs):
        # Rebuild the ltree path from the parent before persisting.
        if self.parent_id is None:
            self.path = self.code
        else:
            parent = Organization.objects.get(pk=self.parent_id)
            self.path = f"{parent.path}.{self.code}"
        super().save(*args, **kwargs)

    def get_ancestors(self, include_self=False):
        """Ancestors of this node (optionally including itself)."""
        qs = Organization.objects.filter(path__ancestors=self.path)
        if not include_self:
            qs = qs.exclude(pk=self.pk)
        return qs

    def get_children(self):
        return Organization.objects.filter(parent=self)

    def get_descendants(self, include_self=False):
        """Descendants of this node (optionally including itself)."""
        qs = Organization.objects.filter(path__descendants=self.path)
        if not include_self:
            qs = qs.exclude(pk=self.pk)
        return qs


class OrganizationMembership(TimeStampedModel, ActiveModel):
    """A person is a member of an organization with a role.

    Design decision: uniqueness is on ``(person, organization, role)`` so a
    person may hold *several* roles in the same organization, each with its own
    ``ServiceAccess`` restrictions.
    """

    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.ForeignKey("access.Role", on_delete=models.PROTECT, related_name="memberships")
    # Optional end-of-membership; null means current membership.
    start_date = models.DateField(_("start date"), null=True, blank=True)
    end_date = models.DateField(_("end date"), null=True, blank=True)

    class Meta:
        verbose_name = _("organization membership")
        verbose_name_plural = _("organization memberships")
        constraints = [
            models.UniqueConstraint(fields=["person", "organization", "role"], name="u_membership"),
        ]
        indexes = [
            models.Index(fields=["organization", "role"]),
            models.Index(fields=["person", "role"]),
        ]

    def __str__(self):
        return f"{self.person} — {self.organization} — {self.role}"


class OrganizationUnit(TimeStampedModel, ActiveModel):
    """Generic internal unit/department of an organization.

    Does not assume a uniform internal structure across organizations — each
    organization may define its own units with the same ltree approach for
    nested units.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="units")
    name = models.CharField(_("name"), max_length=255)
    code = models.SlugField(_("code"), max_length=64)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    path = LTreeField(_("path"), null=True, blank=True, editable=False)
    responsible = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsible_units",
    )

    class Meta:
        verbose_name = _("organization unit")
        verbose_name_plural = _("organization units")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "parent", "code"],
                name="u_unit_org_parent_code",
            ),
        ]
        indexes = [
            GistIndex(name="unit_path_gin_idx", fields=["path"], opclasses=["gist_ltree_ops"]),
        ]

    def __str__(self):
        return f"{self.organization} / {self.name}"

    def save(self, *args, **kwargs):
        if self.parent_id is None:
            self.path = f"{self.organization.code}.{self.code}"
        else:
            parent = OrganizationUnit.objects.get(pk=self.parent_id)
            self.path = f"{parent.path}.{self.code}"
        super().save(*args, **kwargs)


# Authorization: ``Organization`` *is* the scope root, so it reuses the same
# visible_to mechanism but with _AUTH_ORG_FIELD="id" (declared above).
