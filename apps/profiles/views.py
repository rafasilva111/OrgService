"""Profile & settings — the "me" surface of the platform.

  * ``profile``  — read-only summary of the signed-in account (identity,
                  person record, memberships/roles, language);
  * ``settings`` — editable identity (first/last name, e-mail) plus the
                  interface language preference.

Both live under the profile dropdown in the header.
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.common.authorization import membership_service_scope
from apps.common.breadcrumbs import BreadcrumbsMixin

from .models import UserProfile


class SettingsForm(forms.Form):
    """Editable identity fields + interface language."""

    first_name = forms.CharField(
        label=_("First name"),
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        label=_("Last name"),
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    email = forms.EmailField(
        label=_("E-mail"),
        required=False,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    language = forms.ChoiceField(
        label=_("Interface language"),
        choices=[("", _("Platform default"))] + list(settings.LANGUAGES),
        required=False,
    )


class ProfileView(BreadcrumbsMixin, LoginRequiredMixin, TemplateView):
    template_name = "profiles/profile.html"
    breadcrumb_title = _("My profile")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        person = getattr(user, "person", None)
        profile, _ = UserProfile.objects.get_or_create(user=user)

        memberships = list(
            person.memberships.select_related("organization", "role").filter(active=True)
            if person
            else []
        )
        service_roles = [
            {
                "organization": m.organization,
                "role": m.role,
                "unrestricted": not m.service_access.filter(active=True).exists(),
                "services": membership_service_scope(m),
            }
            for m in memberships
        ]
        ctx.update(
            {
                "profile_user": user,
                "person": person,
                "profile": profile,
                "memberships": memberships,
                "service_roles": service_roles,
                "languages": dict(settings.LANGUAGES),
            }
        )
        return ctx


class SettingsView(BreadcrumbsMixin, LoginRequiredMixin, FormView):
    template_name = "profiles/settings.html"
    form_class = SettingsForm
    breadcrumb_title = _("Settings")

    def get_initial(self):
        user = self.request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "language": profile.language,
        }

    def form_valid(self, form):
        user: User = self.request.user
        user.first_name = form.cleaned_data["first_name"]
        user.last_name = form.cleaned_data["last_name"]
        user.email = form.cleaned_data["email"]
        user.save(update_fields=["first_name", "last_name", "email"])

        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.language = form.cleaned_data["language"]
        profile.save(update_fields=["language", "updated_at"])

        active = profile.language or settings.LANGUAGE_CODE
        translation.activate(active)
        self.request.LANGUAGE_CODE = translation.get_language()

        messages.success(self.request, _("Settings saved."))
        return redirect("profiles:settings")
