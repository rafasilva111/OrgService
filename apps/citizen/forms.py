"""Citizen portal forms: sign-up, profile, new request, comment."""

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.offerings.request_form import phone_validator
from apps.organizations.models import Organization, OrganizationType
from apps.people.models import Person, PersonType


def parish_field():
    return forms.ModelChoiceField(
        label=_("Parish of residence"),
        queryset=Organization.objects.alive()
        .filter(active=True, organization_type=OrganizationType.PARISH)
        .order_by("name"),
        required=False,
        empty_label=_("Select your parish"),
    )


def _email_taken(email, *, exclude_person=None, exclude_user=None):
    people = Person.objects.filter(email__iexact=email)
    users = get_user_model().objects.filter(email__iexact=email)
    if exclude_person is not None:
        people = people.exclude(pk=exclude_person.pk)
    if exclude_user is not None:
        users = users.exclude(pk=exclude_user.pk)
    return people.exists() or users.exists()


class CitizenSignupForm(UserCreationForm):
    """Creates a login ``User`` and its ``Person`` (type CITIZEN).

    The email is the username, so citizens sign in with it.
    """

    full_name = forms.CharField(label=_("Full name"), max_length=255)
    email = forms.EmailField(label=_("Email"))
    phone = forms.CharField(
        label=_("Phone"), max_length=32, required=False, validators=[phone_validator]
    )

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("full_name", "email", "phone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["home_organization"] = parish_field()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if _email_taken(email) or get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError(_("An account with this email already exists."))
        return email

    def _post_clean(self):
        # UserCreationForm validates the password against ``self.instance``;
        # give it the username first so similarity checks see the email.
        self.instance.username = self.cleaned_data.get("email", "")
        super()._post_clean()

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        email = self.cleaned_data["email"]
        user.username = email
        user.email = email
        user.first_name = self.cleaned_data["full_name"][:150]
        user.save()
        Person.objects.create(
            user=user,
            full_name=self.cleaned_data["full_name"],
            email=email,
            phone=self.cleaned_data["phone"] or None,
            person_type=PersonType.CITIZEN,
            home_organization=self.cleaned_data.get("home_organization"),
        )
        return user


class CitizenProfileForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=255)
    email = forms.EmailField(label=_("Email"))
    phone = forms.CharField(
        label=_("Phone"), max_length=32, required=False, validators=[phone_validator]
    )
    language = forms.ChoiceField(
        label=_("Language"),
        choices=[("", _("Browser default")), *settings.LANGUAGES],
        required=False,
    )

    def __init__(self, *args, person, **kwargs):
        self.person = person
        super().__init__(*args, **kwargs)
        self.fields["home_organization"] = parish_field()
        self.order_fields(["full_name", "email", "phone", "home_organization", "language"])

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if _email_taken(email, exclude_person=self.person, exclude_user=self.person.user):
            raise forms.ValidationError(_("An account with this email already exists."))
        return email


class CitizenRequestForm(forms.Form):
    """Title and description of a citizen request; the offering comes from the URL."""

    title = forms.CharField(
        label=_("Subject"),
        max_length=255,
        help_text=_("A short summary of what you need."),
    )
    description = forms.CharField(
        label=_("Description"),
        required=False,
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class CitizenCommentForm(forms.Form):
    body = forms.CharField(
        label=_("Message"),
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 4000}),
    )


class CitizenAuthenticationForm(AuthenticationForm):
    """Citizens sign in with their email (it is their username)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Email")

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip().lower()
