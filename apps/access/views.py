"""Access: roles, permissions and their grants."""

from django import forms
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from apps.access.models import Permission, Role
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin
from apps.common.tables.config import Column, TableConfig
from apps.common.tables.views import TableView


class RoleListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Role
    template_name = "access/roles.html"
    platform_codes = ("organization.view",)
    table_config = TableConfig(
        key="roles",
        columns=[
            Column("code", _("Code"), sortable=True, searchable=True),
            Column("name", _("Role"), sortable=True, searchable=True),
            Column("description", _("Description"), searchable=True),
            Column("is_system", _("System"), sortable=True, template="partials/bool.html"),
            Column("permission_count", _("Permissions"), template="access/_permissions_count.html"),
        ],
        default_sort="code",
        caption="Roles",
        empty_headline="No roles yet",
        empty_body="Reusable role definitions will appear here.",
        row_url_name="access:role_detail",
    )
    breadcrumb_title = _("Roles")

    def get_base_queryset(self):
        return Role.objects.alive().annotate(permission_count=Count("permissions", distinct=True))


class RoleDetailView(BreadcrumbsMixin, PermissionRequiredMixin, DetailView):
    model = Role
    template_name = "access/role_detail.html"
    platform_codes = ("organization.view",)
    breadcrumb_parent = (_("Roles"), "access:roles")

    def get_breadcrumb_title(self) -> str:
        return self.object.name

    def get_queryset(self):
        return Role.objects.alive()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["permission_links"] = self.object.rolepermissions.filter(active=True).select_related(
            "permission"
        )
        return ctx


class PermissionListView(BreadcrumbsMixin, PermissionRequiredMixin, ListView):
    model = Permission
    template_name = "access/permissions.html"
    platform_codes = ("organization.view",)
    breadcrumb_title = _("Permissions")

    def get_queryset(self):
        return Permission.objects.order_by("module", "code")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        modules = {}
        for perm in ctx["object_list"]:
            modules.setdefault(perm.module or "other", []).append(perm)
        ctx["modules"] = modules
        return ctx


class RoleCreateForm(forms.ModelForm):
    """New reusable role definition."""

    class Meta:
        model = Role
        fields = ("code", "name", "description")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data["code"].upper()
        return code


class RoleCreateView(CatalogCreateView):
    form_class = RoleCreateForm
    create_perm_code = "role.create"
    breadcrumb_parent = (_("Roles"), "access:roles")
    success_url_name = "access:roles"
    success_message = _("Role created.")
    heading = _("New role")
    intro = _("Define a reusable role; permissions are assigned in the role catalog.")
    submit_label = _("Create role")


class RoleActivateView(LifecycleActionView):
    model = Role
    perm_code = "role.manage"
    scope_action = ""
    verb = "activate"
    success_message = _("Role activated.")
    redirect_url_name = "access:roles"


class RoleDeactivateView(LifecycleActionView):
    model = Role
    perm_code = "role.manage"
    scope_action = ""
    verb = "deactivate"
    success_message = _("Role deactivated.")
    redirect_url_name = "access:roles"


class RoleDeleteView(LifecycleActionView):
    model = Role
    perm_code = "role.delete"
    scope_action = ""
    verb = "delete"
    success_message = _("Role deleted.")
    redirect_url_name = "access:roles"
