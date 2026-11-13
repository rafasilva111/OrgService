"""People: global read-only directory, detail and skills."""

from django import forms
from django.db.models import Count, Prefetch, Q
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from apps.access.models import Role
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.filters import resolve_org_subtree_path
from apps.common.tables.views import TableView
from apps.organizations.models import Organization, OrganizationMembership
from apps.people.models import Person, PersonType, Skill
from apps.services.models import Service, ServiceActor


def _base_person_queryset(_user):
    return Person.objects.all().alive()


def people_organization_filter_choices(_view):
    return [
        (str(organization.pk), organization.name)
        for organization in Organization.objects.alive().order_by("path").only("id", "name")
    ]


def people_service_filter_choices(_view):
    return [
        (str(service.pk), service.name)
        for service in Service.objects.alive().order_by("name").only("id", "name")
    ]


def people_role_filter_choices(_view):
    return [
        (str(role.pk), role.name)
        for role in Role.objects.filter(
            Q(memberships__active=True) | Q(service_actors__active=True),
            active=True,
            deleted_at__isnull=True,
        )
        .distinct()
        .order_by("name")
        .only("id", "name")
    ]


class PersonDirectoryView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Person
    template_name = "people/directory.html"
    platform_codes = ("people.view",)
    use_both_header_scopes = True
    table_config = TableConfig(
        key="people",
        columns=[
            Column("full_name", _("Name"), sortable=True, searchable=True),
            Column("email", _("Email"), sortable=True, searchable=True),
            # Keep directory filters together and in the requested UX order.
            Column(
                "active_memberships",
                _("Organization"),
                template="people/_organizations.html",
                filter=Filter(
                    FilterKind.SUBTREE,
                    param="organization",
                    lookups=("memberships__organization__path",),
                    choices_callable=people_organization_filter_choices,
                    subtree_resolver=resolve_org_subtree_path,
                ),
            ),
            Column(
                "service_actor_links.service.name",
                _("Service"),
                template="people/_services.html",
                filter=Filter(
                    FilterKind.CHOICE,
                    param="service",
                    lookup="service_actor_links__service_id",
                    choices_callable=people_service_filter_choices,
                ),
            ),
            Column(
                "full_name",
                _("Role"),
                template="people/_roles.html",
                filter=Filter(
                    FilterKind.CHOICE,
                    param="role",
                    lookups=(
                        "memberships__role_id",
                        "service_actor_links__role_id",
                    ),
                    choices_callable=people_role_filter_choices,
                ),
            ),
            Column("active", _("Active"), sortable=True, template="partials/bool.html"),
        ],
        default_sort="full_name",
        caption="People",
        empty_headline="No people found",
        empty_body="People in the directory will appear here.",
        row_url_name="people:detail",
    )
    breadcrumb_title = _("Directory")
    organization_filter_fields = ("memberships__organization",)

    def apply_service_filter(self, qs):
        raw_ids = [value for value in self.request.GET.getlist("service") if value.strip()]
        if not raw_ids and self.context_service_pk is not None:
            raw_ids = [str(self.context_service_pk)]
        if not raw_ids:
            return qs
        try:
            service_ids = [int(value) for value in raw_ids]
        except ValueError:
            return qs
        return qs.filter(
            service_actor_links__active=True,
            service_actor_links__service_id__in=service_ids,
        ).distinct()

    def apply_organization_filter(self, qs):
        """Filter to active members in an organization subtree.

        Kept as one join so an inactive membership cannot satisfy the path
        while a different active membership satisfies the flag.
        """
        pk = self.resolve_filter_organization_pk()
        if pk is None:
            return qs
        path = self.resolve_organization_path(pk)
        if not path:
            return qs
        return qs.filter(
            memberships__organization__path__descendants=path,
            memberships__active=True,
        ).distinct()

    def resolve_organization_path(self, pk: int) -> str | None:
        return (
            Organization.objects.alive()
            .filter(pk=pk)
            .values_list("path", flat=True)
            .first()
        )

    def get_base_queryset(self):
        return _base_person_queryset(self.request.user).prefetch_related(
            Prefetch(
                "memberships",
                queryset=OrganizationMembership.objects.filter(
                    active=True,
                    organization__active=True,
                    organization__deleted_at__isnull=True,
                    role__active=True,
                    role__deleted_at__isnull=True,
                )
                .select_related("organization", "role")
                .order_by("organization__path"),
                to_attr="active_memberships",
            ),
            Prefetch(
                "service_actor_links",
                queryset=ServiceActor.objects.filter(
                    active=True,
                    service__active=True,
                    service__deleted_at__isnull=True,
                    role__active=True,
                    role__deleted_at__isnull=True,
                )
                .select_related("service", "role")
                .order_by("service__name", "role__name"),
                to_attr="active_service_actor_links",
            ),
        )


class PersonDetailView(BreadcrumbsMixin, PermissionRequiredMixin, DetailView):
    model = Person
    template_name = "people/detail.html"
    platform_codes = ("people.view",)
    breadcrumb_parent = (_("Directory"), "people:directory")

    def get_breadcrumb_title(self) -> str:
        return self.object.full_name

    def get_queryset(self):
        return _base_person_queryset(self.request.user).prefetch_related("memberships", "skills")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        person = self.object
        ctx["memberships"] = person.memberships.filter(
            active=True,
            organization__active=True,
            organization__deleted_at__isnull=True,
        ).select_related("organization", "role")
        ctx["skills"] = person.skills.select_related("skill").order_by("-level")
        return ctx


class SkillListView(BreadcrumbsMixin, PermissionRequiredMixin, ListView):
    model = Skill
    template_name = "people/skills.html"
    platform_codes = ("people.view",)
    breadcrumb_title = _("Skills")
    breadcrumb_parent = (_("Directory"), "people:directory")

    def get_queryset(self):
        return Skill.objects.annotate(holder_count=Count("person_links", distinct=True)).order_by(
            "name"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["people_count"] = _base_person_queryset(self.request.user).count()
        return ctx


class PersonCreateForm(forms.ModelForm):
    """New directory entry (no login account required)."""

    class Meta:
        model = Person
        fields = ("full_name", "email", "person_type")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)


class PersonCreateView(CatalogCreateView):
    form_class = PersonCreateForm
    create_perm_code = "people.create"
    breadcrumb_parent = (_("Directory"), "people:directory")
    success_url_name = "people:directory"
    success_message = _("Person created.")
    heading = _("New person")
    intro = _("Add a person to the people directory. A login account is created separately.")
    submit_label = _("Create person")


class PersonActivateView(LifecycleActionView):
    model = Person
    perm_code = "people.manage"
    scope_action = ""
    verb = "activate"
    success_message = _("Person activated.")
    redirect_url_name = "people:directory"


class PersonDeactivateView(LifecycleActionView):
    model = Person
    perm_code = "people.manage"
    scope_action = ""
    verb = "deactivate"
    success_message = _("Person deactivated.")
    redirect_url_name = "people:directory"


class PersonDeleteView(LifecycleActionView):
    model = Person
    perm_code = "people.delete"
    scope_action = ""
    verb = "delete"
    success_message = _("Person deleted.")
    redirect_url_name = "people:directory"
