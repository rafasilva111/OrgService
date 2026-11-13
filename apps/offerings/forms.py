"""Service offering editor form (offering fields + the work plan as JSON)."""

from django import forms
from django.utils.dates import MONTHS, WEEKDAYS
from django.utils.translation import gettext_lazy as _

from apps.access.models import Role
from apps.common.durations import duration_input
from apps.common.mixins import visible_services
from apps.offerings.models import (
    RECURRENCE_FIELDS,
    OfferingStatus,
    RecurrenceFrequency,
    ServiceOffering,
    ServiceOfferingType,
)


def manageable_services(user):
    """Services the user may create and edit offerings for (``offering.manage``)."""
    from apps.services.models import Service

    return Service.objects.visible_to(user, action="offering.manage").alive()


def step_role_choices():
    """Roles a step can be assigned to (base roles, not per-service clones)."""
    return (
        Role.objects.filter(active=True, deleted_at__isnull=True)
        .exclude(code__startswith="SERVICE_MANAGER_")
        .order_by("name")
    )


def service_step_roles(services):
    """Active shared roles configured on the given services through service actors."""
    from apps.services.models import ServiceActor

    roles = {}
    actors = (
        ServiceActor.objects.filter(
            service__in=services, active=True, role__active=True, role__deleted_at__isnull=True
        )
        .exclude(role__code__startswith="SERVICE_MANAGER_")
        .select_related("role")
        .order_by("role__name", "role_id")
    )
    for actor in actors:
        roles.setdefault(actor.service_id, {})[actor.role_id] = actor.role
    return {service_id: list(by_id.values()) for service_id, by_id in roles.items()}


def responsible_offering_choices(user):
    """ACTIVE offerings (of any service the user can see) a step can hand off to."""
    return (
        ServiceOffering.objects.filter(
            service__in=visible_services(user), status=OfferingStatus.ACTIVE
        )
        .select_related("service", "service__organization", "offering_type")
        .order_by("service__name", "name")
    )


LOCATION_TYPES = ("DISTRICT", "MUNICIPALITY", "PARISH")


def location_options(offerings, services=()):
    """Cascade data for picking a responsible offering by location.

    Returns ``(organizations, offerings, service_chains)`` for the editor:
      * organizations — districts, municipalities and parishes that contain at
        least one of ``offerings``: ``{id, name, type, ancestors}`` where
        ``ancestors`` are the ids of their location ancestors;
      * offerings — ``[id, label, [location ids], service id, total SLA (text),
        type name, description]``;
      * service_chains — ``{service id: [location ids]}`` for ``services`` (the
        services the offering itself can belong to), used to pre-fill the cascade
        with the offering's own location.
    One query for the organizations; ancestry comes from the ltree paths.
    """
    from apps.organizations.models import Organization

    offerings = list(offerings)
    by_path = {
        str(o.path): o
        for o in Organization.objects.alive()
        .filter(organization_type__in=LOCATION_TYPES)
        .only("id", "name", "organization_type", "path")
    }

    def location_chain(path):
        labels = str(path).split(".")
        prefixes = (".".join(labels[: i + 1]) for i in range(len(labels)))
        return [by_path[p] for p in prefixes if p in by_path]

    used, offering_rows = {}, []
    for offering in offerings:
        chain = location_chain(offering.service.organization.path)
        for org in chain:
            used[org.pk] = org
        offering_rows.append(
            [
                offering.pk,
                f"{offering.service.name} · {offering.name}",
                [o.pk for o in chain],
                offering.service_id,
                duration_input(offering.sla_minutes),
                offering.offering_type.name
                if offering.offering_type_id
                else str(_("To be defined")),
                (offering.description or "")[:400],
            ]
        )
    service_chains = {}
    for service in services:
        chain = location_chain(service.organization.path)
        for org in chain:
            used[org.pk] = org
        service_chains[str(service.pk)] = [o.pk for o in chain]
    organizations = []
    for org in sorted(used.values(), key=lambda o: o.name):
        ancestors = [o.pk for o in location_chain(org.path) if o.pk != org.pk]
        organizations.append(
            {"id": org.pk, "name": org.name, "type": org.organization_type, "ancestors": ancestors}
        )
    return organizations, offering_rows, service_chains


class ServiceOfferingForm(forms.ModelForm):
    definition = forms.CharField(widget=forms.HiddenInput, required=False)
    recurrence_weekdays = forms.TypedMultipleChoiceField(
        label=_("On"),
        choices=[(k, v) for k, v in WEEKDAYS.items()],
        coerce=int,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    recurrence_month_day = forms.TypedChoiceField(
        label=_("Day of month"),
        choices=[("", "—")] + [(d, d) for d in range(1, 32)],
        coerce=int,
        empty_value=None,
        required=False,
    )
    recurrence_month = forms.TypedChoiceField(
        label=_("Month"),
        choices=[("", "—")] + list(MONTHS.items()),
        coerce=int,
        empty_value=None,
        required=False,
    )

    class Meta:
        model = ServiceOffering
        fields = (
            "service",
            "name",
            "offering_type",
            "description",
            "default_priority",
            "on_request",
            "on_schedule",
            *RECURRENCE_FIELDS,
        )
        widgets = {
            "on_request": forms.CheckboxInput(attrs={"x-model": "onRequest"}),
            "on_schedule": forms.CheckboxInput(attrs={"x-model": "onSchedule"}),
            "recurrence_frequency": forms.Select(
                attrs={
                    "x-model": "frequency",
                    "class": "rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-indigo-500",
                }
            ),
            "recurrence_interval": forms.NumberInput(
                attrs={
                    "min": 1,
                    "max": 99,
                    "x-model.number": "interval",
                    "class": "w-20 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-indigo-500",
                }
            ),
            "recurrence_start": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "recurrence_end": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = (
            manageable_services(user).select_related("organization").order_by("name")
        )
        self.fields["service"].label_from_instance = lambda s: f"{s.name} — {s.organization.name}"
        self.fields["service"].widget.attrs["x-model"] = "service"  # editor state
        self.fields["offering_type"].queryset = ServiceOfferingType.objects.order_by("name")
        # Optional: "To be defined" (no type yet) is the empty choice.
        self.fields["offering_type"].required = False
        self.fields["offering_type"].empty_label = _("To be defined")
        self.fields["description"].widget.attrs["rows"] = 3
        self.fields["on_request"].label = _("On request")
        self.fields["on_request"].help_text = _("Citizens can ask for it in New request.")
        self.fields["on_schedule"].label = _("Recurring")
        self.fields["on_schedule"].help_text = _("It also runs on a repeating schedule.")
        self.fields["recurrence_frequency"].choices = [("", "—"), *RecurrenceFrequency.choices]
        self.fields["recurrence_interval"].required = False
        # A later version belongs to an existing (service, name) family.
        if self.instance.pk and self.instance.version > 1:
            for name in ("service", "name"):
                self.fields[name].disabled = True
                self.fields[name].help_text = _("Fixed for new versions of an offering.")

    def clean(self):
        cleaned = super().clean()
        self._clean_recurrence(cleaned)
        service, name = cleaned.get("service"), cleaned.get("name")
        if service and name:
            clash = ServiceOffering.objects.filter(service=service, name__iexact=name)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
                if self.instance.version > 1:
                    clash = clash.none()
            if clash.exists():
                self.add_error(
                    "name",
                    _(
                        "This service already has an offering with this name — open it and "
                        "create a new version instead."
                    ),
                )
        return cleaned

    def _clean_recurrence(self, cleaned):
        """At least one way to start; a recurring offering needs a complete schedule."""
        if not cleaned.get("on_request") and not cleaned.get("on_schedule"):
            self.add_error("on_request", _("Choose at least one: on request and/or recurring."))
        if not cleaned.get("on_schedule"):
            cleaned.update(
                recurrence_frequency="",
                recurrence_interval=1,
                recurrence_weekdays=[],
                recurrence_month_day=None,
                recurrence_month=None,
                recurrence_start=None,
                recurrence_end=None,
            )
            return
        freq = cleaned.get("recurrence_frequency")
        cleaned["recurrence_interval"] = cleaned.get("recurrence_interval") or 1
        required = _("Required for recurring offerings.")
        if not freq:
            self.add_error("recurrence_frequency", required)
        if not cleaned.get("recurrence_start"):
            self.add_error("recurrence_start", required)
        if freq == RecurrenceFrequency.WEEKLY and not cleaned.get("recurrence_weekdays"):
            self.add_error("recurrence_weekdays", _("Pick at least one day of the week."))
        if freq in (RecurrenceFrequency.MONTHLY, RecurrenceFrequency.YEARLY) and not cleaned.get(
            "recurrence_month_day"
        ):
            self.add_error("recurrence_month_day", required)
        if freq == RecurrenceFrequency.YEARLY and not cleaned.get("recurrence_month"):
            self.add_error("recurrence_month", required)
        if freq != RecurrenceFrequency.WEEKLY:
            cleaned["recurrence_weekdays"] = []
        if freq not in (RecurrenceFrequency.MONTHLY, RecurrenceFrequency.YEARLY):
            cleaned["recurrence_month_day"] = None
        if freq != RecurrenceFrequency.YEARLY:
            cleaned["recurrence_month"] = None
        start, end = cleaned.get("recurrence_start"), cleaned.get("recurrence_end")
        if start and end and end < start:
            self.add_error("recurrence_end", _("Must be on or after the start date."))
