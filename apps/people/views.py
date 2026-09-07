"""People: directory (members of visible orgs), detail and skills."""

from django import forms
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin, visible_org_ids
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.organizations.models import OrganizationMembership
from apps.people.models import Person, PersonType, Skill


def _base_person_queryset(user):
    if getattr(user, "is_superuser", False):
        return Person.objects.all().alive()
    member_person_ids = list(
        OrganizationMembership.objects.filter(organization_id__in=visible_org_ids(user))
        .values_list("person_id", flat=True)
        .distinct()
    )
    return Person.objects.filter(id__in=member_person_ids).alive()


class PersonDirectoryView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Person
    template_name = "people/directory.html"
    platform_codes = ("people.view",)
    table_config = TableConfig(
        key="people",
        columns=[
            Column("full_name", _("Name"), sortable=True, searchable=True),
            Column("email", _("Email"), sortable=True, searchable=True),
            Column(
                "person_type",
                _("Type"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=PersonType.choices),
                template="partials/pill.html",
            ),
            Column("active", _("Active"), sortable=True, template="partials/bool.html"),
        ],
        default_sort="full_name",
        caption="People",
        empty_headline="No people in scope",
        empty_body="People with memberships in organizations you can see will appear here.",
        row_url_name="people:detail",
    )
    breadcrumb_title = _("Directory")

    def get_base_queryset(self):
        return _base_person_queryset(self.request.user)


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
        ctx["memberships"] = person.memberships.filter(active=True).select_related(
            "organization", "role"
        )
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
