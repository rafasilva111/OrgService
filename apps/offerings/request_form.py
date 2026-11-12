"""Per-offering request form: builder definition ⇄ OfferingFormField rows ⇄ a
Django form for Novo Pedido ⇄ answers stored on ``Request.form_data``.

Field names in the rendered form are ``q_<key>``; an Address question is three
inputs ``q_<key>__street`` / ``__postal_code`` / ``__locality``.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import transaction
from django.template.defaultfilters import filesizeformat
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.offerings.models import FormFieldType, OfferingFormField

CHOICE_TYPES = (FormFieldType.CHOICE, FormFieldType.MULTICHOICE)
ADDRESS_PARTS = ("street", "postal_code", "locality")

phone_validator = RegexValidator(
    r"^\+?[0-9][0-9 ]{7,17}$", gettext_lazy("Enter a valid phone number.")
)
postal_code_validator = RegexValidator(r"^\d{4}-\d{3}$", gettext_lazy("Use the format 1234-567."))


@dataclass
class FormFieldSpec:
    key: str
    label: str
    field_type: str = FormFieldType.TEXT
    required: bool = False
    help_text: str = ""
    options: list = field(default_factory=list)


# ------------------------------------------------------------- definition
def parse_form_definition(raw):
    """Validate the builder JSON. Returns ``(specs, errors)`` where ``errors``
    maps the field index to messages (and ``-1`` to form-wide messages)."""
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return [], {-1: [_("The request form could not be read.")]}
    if not isinstance(data, list):
        return [], {-1: [_("The request form could not be read.")]}
    specs, errors, taken = [], {}, set()
    for index, item in enumerate(data):
        item = item if isinstance(item, dict) else {}
        problems = []
        label = str(item.get("label") or "").strip()[:255]
        if not label:
            problems.append(_("Give the question a label."))
        field_type = item.get("field_type") or FormFieldType.TEXT
        if field_type not in FormFieldType.values:
            problems.append(_("Unknown field type."))
            field_type = FormFieldType.TEXT
        options = item.get("options") or []
        if isinstance(options, str):
            options = options.splitlines()
        options = list(dict.fromkeys(str(o).strip() for o in options if str(o).strip()))
        if field_type in CHOICE_TYPES and len(options) < 2:
            problems.append(_("A choice needs at least two options (one per line)."))
        if field_type not in CHOICE_TYPES:
            options = []
        # Keep the key stable across edits/versions so answers stay comparable.
        key = slugify(str(item.get("key") or label))[:48] or f"field-{index + 1}"
        base, n = key, 2
        while key in taken:
            key = f"{base}-{n}"
            n += 1
        taken.add(key)
        specs.append(
            FormFieldSpec(
                key=key,
                label=label or f"{_('Question')} {index + 1}",
                field_type=field_type,
                required=bool(item.get("required")),
                help_text=str(item.get("help_text") or "").strip()[:255],
                options=options,
            )
        )
        if problems:
            errors[index] = problems
    return specs, errors


@transaction.atomic
def save_form_definition(offering, specs):
    """Replace a draft offering's request form."""
    if not offering.is_editable:
        raise ValidationError(_("Only draft offerings can be edited."))
    offering.form_fields.all().delete()
    OfferingFormField.objects.bulk_create(
        OfferingFormField(
            offering=offering,
            key=spec.key,
            label=spec.label,
            field_type=spec.field_type,
            required=spec.required,
            help_text=spec.help_text,
            options=spec.options,
            order=(index + 1) * 10,
        )
        for index, spec in enumerate(specs)
    )


def form_definition_from_offering(offering):
    return [
        {
            "key": f.key,
            "label": f.label,
            "field_type": f.field_type,
            "required": f.required,
            "help_text": f.help_text,
            "options": "\n".join(f.options or []),
        }
        for f in offering.form_fields.order_by("order")
    ]


# ------------------------------------------------------------ Django form
def _validate_attachment(upload):
    ext = Path(upload.name).suffix.lower().lstrip(".")
    allowed = settings.REQUEST_ATTACHMENT_EXTENSIONS
    if ext not in allowed:
        raise ValidationError(_("Accepted file types: %(types)s.") % {"types": ", ".join(allowed)})
    if upload.size > settings.REQUEST_ATTACHMENT_MAX_BYTES:
        raise ValidationError(
            _("The file is too large (maximum %(size)s).")
            % {"size": filesizeformat(settings.REQUEST_ATTACHMENT_MAX_BYTES)}
        )


def _django_fields(spec):
    """``[(name, form field)]`` for one question."""
    name = f"q_{spec.key}"
    common = {"label": spec.label, "required": spec.required, "help_text": spec.help_text}
    t = spec.field_type
    if t == FormFieldType.TEXTAREA:
        return [(name, forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), **common))]
    if t == FormFieldType.NUMBER:
        return [(name, forms.DecimalField(**common))]
    if t == FormFieldType.DATE:
        return [(name, forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), **common))]
    if t == FormFieldType.BOOLEAN:
        return [
            (
                name,
                forms.TypedChoiceField(
                    choices=[("yes", _("Yes")), ("no", _("No"))],
                    coerce=lambda v: v == "yes",
                    empty_value=None,
                    widget=forms.RadioSelect,
                    **common,
                ),
            )
        ]
    if t == FormFieldType.EMAIL:
        return [(name, forms.EmailField(**common))]
    if t == FormFieldType.PHONE:
        return [
            (
                name,
                forms.CharField(
                    validators=[phone_validator],
                    widget=forms.TextInput(attrs={"type": "tel"}),
                    **common,
                ),
            )
        ]
    if t == FormFieldType.CHOICE:
        return [
            (
                name,
                forms.ChoiceField(choices=[("", "—")] + [(o, o) for o in spec.options], **common),
            )
        ]
    if t == FormFieldType.MULTICHOICE:
        return [
            (
                name,
                forms.MultipleChoiceField(
                    choices=[(o, o) for o in spec.options],
                    widget=forms.CheckboxSelectMultiple,
                    **common,
                ),
            )
        ]
    if t == FormFieldType.FILE:
        common["help_text"] = (spec.help_text + " — " if spec.help_text else "") + _(
            "%(types)s, up to %(size)s"
        ) % {
            "types": ", ".join(settings.REQUEST_ATTACHMENT_EXTENSIONS),
            "size": filesizeformat(settings.REQUEST_ATTACHMENT_MAX_BYTES),
        }
        return [(name, forms.FileField(validators=[_validate_attachment], **common))]
    if t == FormFieldType.ADDRESS:
        return [
            (
                f"{name}__street",
                forms.CharField(label=_("Street and number"), required=spec.required),
            ),
            (
                f"{name}__postal_code",
                forms.CharField(
                    label=_("Postal code"),
                    required=False,
                    validators=[postal_code_validator],
                    widget=forms.TextInput(attrs={"placeholder": "1234-567"}),
                ),
            ),
            (f"{name}__locality", forms.CharField(label=_("Locality"), required=spec.required)),
        ]
    return [(name, forms.CharField(max_length=500, **common))]


class RequestAnswersForm(forms.Form):
    """Questions of one offering. ``groups()`` yields ``(spec, kind, [bound])``
    for the template (kind: ``plain`` / ``choices`` / ``address``)."""

    def __init__(self, specs, *args, disabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.specs = list(specs)
        self._names = {}
        for spec in self.specs:
            pairs = _django_fields(spec)
            self._names[spec.key] = [n for n, _f in pairs]
            for name, form_field in pairs:
                form_field.disabled = disabled
                self.fields[name] = form_field

    def groups(self):
        for spec in self.specs:
            bound = [self[n] for n in self._names[spec.key]]
            if spec.field_type == FormFieldType.ADDRESS:
                kind = "address"
            elif spec.field_type in (FormFieldType.BOOLEAN, FormFieldType.MULTICHOICE):
                kind = "choices"
            else:
                kind = "plain"
            yield spec, kind, bound

    def answers(self):
        """``(form_data, files)`` from a valid form: JSON-safe answers, plus the
        uploads as ``[(key, UploadedFile)]`` to be stored as attachments."""
        data, files = {}, []
        for spec in self.specs:
            name = f"q_{spec.key}"
            if spec.field_type == FormFieldType.ADDRESS:
                parts = {p: self.cleaned_data.get(f"{name}__{p}") or "" for p in ADDRESS_PARTS}
                if any(parts.values()):
                    data[spec.key] = parts
                continue
            value = self.cleaned_data.get(name)
            if spec.field_type == FormFieldType.FILE:
                if value:
                    files.append((spec.key, value))
                continue
            if value in (None, "", []):
                continue
            if isinstance(value, Decimal):
                value = str(value)
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            data[spec.key] = value
        return data, files


def answers_form_for(offering, data=None, files=None, *, disabled=False):
    return RequestAnswersForm(
        offering.form_fields.order_by("order"), data=data, files=files, disabled=disabled
    )


# -------------------------------------------------------------- display
def answer_rows(request):
    """``[{"label", "text", "attachment"}]`` for a request's answers, in the
    order of the offering's questions (unknown keys are listed last)."""
    from datetime import date

    from django.utils.formats import date_format

    data = dict(request.form_data or {})
    attachments = {a.field_key: a for a in request.attachments.all()}
    rows = []
    specs = list(request.offering.form_fields.order_by("order")) if request.offering_id else []
    for spec in specs:
        attachment = attachments.pop(spec.key, None)
        value = data.pop(spec.key, None)
        if attachment is None and value in (None, "", [], {}):
            continue
        text = ""
        if spec.field_type == FormFieldType.BOOLEAN:
            text = _("Yes") if value else _("No")
        elif spec.field_type == FormFieldType.DATE and value:
            text = date_format(date.fromisoformat(value), "SHORT_DATE_FORMAT")
        elif spec.field_type == FormFieldType.ADDRESS and isinstance(value, dict):
            locality = " ".join(p for p in (value.get("postal_code"), value.get("locality")) if p)
            text = ", ".join(p for p in (value.get("street"), locality) if p)
        elif isinstance(value, list):
            text = ", ".join(value)
        elif value is not None:
            text = str(value)
        rows.append({"label": spec.label, "text": text, "attachment": attachment})
    for key, value in data.items():
        rows.append({"label": key, "text": str(value), "attachment": None})
    for attachment in attachments.values():
        rows.append({"label": attachment.field_key, "text": "", "attachment": attachment})
    return rows
