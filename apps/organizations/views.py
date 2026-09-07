"""Organizations: hierarchy list, subtree tree, memberships and detail."""

from django import forms
from django.db.models import Prefetch
from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin, ScopedDetailView, ScopedListView
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.organizations.models import (
    Organization,
    OrganizationMembership,
    OrganizationType,
)
from apps.requests.models import Request, RequestStatus
from apps.services.models import Service, ServiceActor

CLOSED_STATUSES = (RequestStatus.RESOLVED, RequestStatus.CLOSED, RequestStatus.CANCELLED)


ORG_COLUMNS = [
    Column("name", _("Organization"), sortable=True, searchable=True),
    Column("code", _("Code"), sortable=True),
    Column(
        "organization_type",
        _("Type"),
        filter=Filter(FilterKind.CHOICE, choices=OrganizationType.choices),
    ),
    Column("path", _("Path")),
]


class OrganizationListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Organization
    template_name = "organizations/index.html"
    platform_codes = ("organization.view",)
    table_config = TableConfig(
        key="organizations",
        columns=ORG_COLUMNS,
        default_sort="name",
        caption="Organizations",
        empty_headline="No organizations in scope",
        empty_body="Organizations you can see will appear here.",
        row_url_name="organizations:detail",
    )
    breadcrumb_title = _("Organizations")

    def get_base_queryset(self):
        return Organization.objects.visible_to(self.request.user, "organization.view").alive()


class OrganizationDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Organization
    template_name = "organizations/detail.html"
    scope_action = "organization.view"

    def get_breadcrumb_title(self) -> str:
        return self.object.name

    def get_queryset(self):
        return super().get_queryset().alive()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = self.object

        visible_orgs = Organization.objects.visible_to(self.request.user, "organization.view")
        descendants = org.get_descendants(include_self=True)
        desc_ids = descendants.values_list("pk", flat=True)

        subtree_visible_orgs = visible_orgs.filter(pk__in=desc_ids)
        services_visible = (
            Service.objects.visible_to(self.request.user, "service.view")
            .filter(organization_id__in=desc_ids)
            .select_related("organization")
            .order_by("name")
        )
        ctx["children"] = (
            org.get_children().filter(pk__in=subtree_visible_orgs.values("pk")).order_by("name")
        )
        ctx["services"] = services_visible
        ctx["visible_org_count"] = subtree_visible_orgs.count()
        ctx["members_count"] = (
            OrganizationMembership.objects.filter(organization_id__in=desc_ids, active=True)
            .distinct("person_id")
            .count()
        )
        ctx["open_request_count"] = (
            Request.objects.visible_to(self.request.user, "request.view")
            .filter(service__organization_id__in=desc_ids)
            .exclude(status__in=CLOSED_STATUSES)
            .count()
        )
        ctx["recent_memberships"] = (
            OrganizationMembership.objects.filter(organization_id__in=desc_ids, active=True)
            .select_related("person", "organization", "role")
            .order_by("-created_at")[:10]
        )
        return ctx


class OrganizationTreeView(BreadcrumbsMixin, ScopedListView):
    model = Organization
    template_name = "organizations/tree.html"
    scope_action = "organization.view"
    breadcrumb_title = _("Organization tree")
    breadcrumb_parent = (_("Organizations"), "organizations:index")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["object_list"] = self.visible_tree()
        return ctx

    def visible_tree(self):
        """Visible orgs as a nested list, each node with ``children`` and
        ``services`` (services carrying their ``actors`` roles prefetched)."""
        user = self.request.user

        orgs = list(
            Organization.objects.visible_to(user, "organization.view")
            .alive()
            .prefetch_related(
                Prefetch(
                    "services",
                    Service.objects.visible_to(user, "service.view")
                    .alive()
                    .prefetch_related(
                        Prefetch(
                            "actors",
                            ServiceActor.objects.select_related("role", "person").order_by(
                                "role__name"
                            ),
                        )
                    )
                    .order_by("name"),
                )
            )
            .order_by("name")
        )

        visible_ids = {org.pk for org in orgs}
        children_by_parent = {}
        for org in orgs:
            children_by_parent.setdefault(org.parent_id, []).append(org)

        def build(node):
            node.tree_children = children_by_parent.get(node.pk, [])
            for child in node.tree_children:
                build(child)
            return node

        roots = []
        for org in orgs:
            if org.parent_id is None or org.parent_id not in visible_ids:
                roots.append(build(org))
        return roots


class MembershipListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = OrganizationMembership
    template_name = "organizations/memberships.html"
    platform_codes = ("organization.view",)
    table_config = TableConfig(
        key="memberships",
        columns=[
            Column("person.full_name", _("Person"), sortable=True, searchable=True),
            Column("organization.name", _("Organization"), sortable=True, searchable=True),
            Column(
                "role.name",
                _("Role"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column("start_date", _("Start"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("end_date", _("End"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
        ],
        default_sort="person.full_name",
        caption="Organization memberships",
        empty_headline="No memberships in scope",
        empty_body="Memberships bound to organizations you can see will appear here.",
    )
    breadcrumb_title = _("Memberships")
    breadcrumb_parent = (_("Organizations"), "organizations:index")

    def get_base_queryset(self):
        visible_orgs = Organization.objects.visible_to(self.request.user, "organization.view")
        return OrganizationMembership.objects.filter(
            organization__in=visible_orgs, active=True
        ).select_related("person", "organization", "role")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["open_membership_count"] = self.get_base_queryset().count()
        return ctx


class OrganizationCreateForm(forms.ModelForm):
    """New organization; parents are scoped to organizations the user may see."""

    class Meta:
        model = Organization
        fields = ("name", "code", "organization_type", "parent", "description")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(user, "is_superuser", False):
            choices = Organization.objects.all()
        else:
            choices = Organization.objects.visible_to(user, "organization.view")
        self.fields["parent"].queryset = choices
        self.fields["parent"].label = _("Parent organization")
        self.fields["parent"].help_text = _("Leave empty for a root organization.")


class OrganizationCreateView(CatalogCreateView):
    form_class = OrganizationCreateForm
    create_perm_code = "organization.create"
    breadcrumb_parent = (_("Organizations"), "organizations:index")
    success_url_name = "organizations:index"
    success_message = _("Organization created.")
    heading = _("New organization")
    intro = _(
        "Create an organizational unit (district, municipality, parish…). Its code is part of the tree path."
    )
    submit_label = _("Create organization")


class OrganizationActivateView(LifecycleActionView):
    model = Organization
    perm_code = "organization.manage"
    scope_action = "organization.view"
    verb = "activate"
    success_message = _("Organization activated.")
    redirect_url_name = "organizations:index"


class OrganizationDeactivateView(LifecycleActionView):
    model = Organization
    perm_code = "organization.manage"
    scope_action = "organization.view"
    verb = "deactivate"
    success_message = _("Organization deactivated.")
    redirect_url_name = "organizations:index"


class OrganizationDeleteView(LifecycleActionView):
    model = Organization
    perm_code = "organization.delete"
    scope_action = "organization.view"
    verb = "delete"
    success_message = _("Organization deleted.")
    redirect_url_name = "organizations:index"
