"""Access: roles, permissions and their grants."""

from django import forms
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from apps.access.models import Permission, Role
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin
from apps.common.tables.config import Column, TableConfig
from apps.common.tables.views import TableView
from apps.organizations.models import Organization, OrganizationMembership
from apps.services.models import ServiceActor


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
        from apps.common.context_processors import _visible_services_list, user_organization_pks
        from apps.common.mixins import selected_service_pk, visible_orgs

        user, session = self.request.user, self.request.session
        org_pk = (session.get("org_context") or {}).get("pk")
        if org_pk not in user_organization_pks(user):
            org_pk = None
        org = Organization.objects.alive().filter(pk=org_pk).first() if org_pk is not None else None
        if org is None:
            org_pk = None
        service_pk = selected_service_pk(user, session) if org_pk is not None else None
        service_context = session.get("service_context") or {}
        service = None
        if service_pk is not None:
            service = next(
                (
                    item
                    for item in _visible_services_list(user, org_pk=org_pk)
                    if item["pk"] == service_pk and item["role_code"] == service_context.get("role")
                ),
                None,
            )

        roles = Role.objects.alive()
        self.role_scope = "global"
        self.role_scope_name = ""
        if service is not None:
            self.role_scope, self.role_scope_name = "service", service["name"]
            actors = ServiceActor.objects.filter(service_id=service_pk, active=True)
            roles = roles.filter(active=True, pk__in=actors.values("role_id")).exclude(
                code__startswith="SERVICE_MANAGER_"
            )
        elif org_pk is not None:
            from apps.common.mixins import visible_services

            self.role_scope, self.role_scope_name = "organization", org.name
            organizations = visible_orgs(user).alive().filter(path__descendants=org.path)
            services = visible_services(user).alive().filter(organization__in=organizations)
            actors = ServiceActor.objects.filter(service__in=services, active=True)
            memberships = OrganizationMembership.objects.filter(
                organization__in=organizations,
                active=True,
                person__active=True,
                person__deleted_at__isnull=True,
            )
            roles = roles.filter(active=True).filter(
                Q(pk__in=actors.values("role_id")) | Q(pk__in=memberships.values("role_id"))
            )
        return roles.annotate(permission_count=Count("permissions", distinct=True))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["role_scope"] = self.role_scope
        context["role_scope_name"] = self.role_scope_name
        return context


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
