from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.organizations.models import Organization, OrganizationMembership, OrganizationUnit


class DeletedFilter(admin.SimpleListFilter):
    """Soft-delete filter with explicit labels.

    ``admin.EmptyFieldListFilter`` would work too, but renders as
    "Empty / Not empty", which is unreadable next to a ``deleted_at`` column.
    """

    title = _("deleted")
    parameter_name = "deleted"

    def lookups(self, request, model_admin):
        return (("no", _("Not deleted")), ("yes", _("Deleted")))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(deleted_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(deleted_at__isnull=True)
        return queryset


@admin.action(description=_("Activate selected %(verbose_name_plural)s"))
def activate_selected(modeladmin, request, queryset):
    queryset.update(active=True)


@admin.action(description=_("Deactivate selected %(verbose_name_plural)s"))
def deactivate_selected(modeladmin, request, queryset):
    queryset.update(active=False)


@admin.action(description=_("Soft-delete selected %(verbose_name_plural)s"))
def soft_delete_selected(modeladmin, request, queryset):
    queryset.filter(deleted_at__isnull=True).update(deleted_at=timezone.now())


@admin.action(description=_("Restore selected %(verbose_name_plural)s"))
def restore_selected(modeladmin, request, queryset):
    queryset.filter(deleted_at__isnull=False).update(deleted_at=None)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization_type", "parent", "active", "deleted_at")
    list_filter = ("organization_type", "active", DeletedFilter)
    search_fields = ("name", "code", "description")
    list_select_related = ("parent",)
    autocomplete_fields = ("parent",)
    readonly_fields = ("path", "created_at", "updated_at")
    actions = (activate_selected, deactivate_selected, soft_delete_selected, restore_selected)

    def has_delete_permission(self, request, obj=None):
        """Forbid hard delete; the catalog contract is soft delete only.

        A hard delete would cascade into ``Service``, ``OrganizationMembership``,
        ``OrganizationUnit`` and child organizations (all ``on_delete=CASCADE``),
        silently destroying the subtree. Use the soft-delete action instead —
        see ``apps/common/lifecycle.py`` and ADR 009.
        """
        return False


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("person", "organization", "role", "start_date", "end_date", "active")
    list_filter = ("active", "role", "organization")
    search_fields = ("person__full_name", "organization__name", "role__name")
    list_select_related = ("person", "organization", "role")
    readonly_fields = ("created_at", "updated_at")
    actions = (activate_selected, deactivate_selected)

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


@admin.register(OrganizationUnit)
class OrganizationUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "parent", "responsible", "active")
    list_filter = ("active", "organization")
    search_fields = ("name", "code", "responsible__full_name")
    list_select_related = ("organization", "parent", "responsible")
    autocomplete_fields = ("parent",)
    readonly_fields = ("path", "created_at", "updated_at")
    actions = (activate_selected, deactivate_selected)
