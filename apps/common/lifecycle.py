"""Generic catalog lifecycle views: create plus activate/deactivate/delete.

Reused by the catalog apps (organizations, services, people, resources and
roles). Records are soft-deleted — the default manager keeps ``all()``
semantics, so every lookup/list must pass through ``.alive()`` to keep deleted
rows out of the UI.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, View

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import CreatePermissionMixin, user_has_permission_code


class LifecycleActionView(LoginRequiredMixin, View):
    """POST-only activate/deactivate/delete action on a single catalog row.

    Attributes (set by subclasses):
      model             — the catalog model to act on
      perm_code         — flat permission code required (e.g. ``"service.manage"``)
      scope_action      — ``visible_to`` action used to scope the target row
                          (empty for unscoped models such as Role/Person)
      verb              — ``"activate" | "deactivate" | "delete"``
      success_message   — message shown after the action
      redirect_url_name — URL name to redirect to after success
    """

    model = None
    perm_code = ""
    scope_action = ""
    verb = ""
    success_message = ""
    redirect_url_name = ""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.perm_code and not user_has_permission_code(request.user, self.perm_code):
            raise PermissionDenied
        if request.method != "POST":
            return self.http_method_not_allowed(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, **kwargs):
        qs = self.model.objects.all()
        if self.scope_action:
            qs = qs.visible_to(self.request.user, action=self.scope_action)
        return get_object_or_404(qs.alive(), pk=kwargs["pk"])

    def post(self, request, *args, **kwargs):
        obj = self.get_object(**kwargs)
        if self.verb == "activate":
            obj.active = True
            obj.save(update_fields=["active"])
        elif self.verb == "deactivate":
            obj.active = False
            obj.save(update_fields=["active"])
        elif self.verb == "delete":
            obj.soft_delete()
        else:
            raise ValueError(f"Unsupported lifecycle verb: {self.verb!r}")
        if self.success_message:
            messages.success(request, self.success_message)
        return redirect(self.redirect_url_name)


class CatalogCreateView(BreadcrumbsMixin, CreatePermissionMixin, FormView):
    """Base class for the catalog create pages.

    Subclasses set:
      form_class          — a ``ModelForm``
      create_perm_code    — permission code gate (e.g. ``"service.create"``)
      breadcrumb_parent   — (title, url_name) of the parent list page
      success_url_name    — URL name to redirect to after saving
      success_message     — message shown after saving
      heading             — h1 shown on the page
      intro               — short explanatory paragraph
    """

    template_name = "catalog/create.html"
    form_class = None
    create_perm_code = ""
    success_url_name = ""
    success_message = ""
    heading = ""
    intro = ""
    submit_label = _("Save")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_back_url(self):
        parent = self.breadcrumb_parent
        if isinstance(parent, tuple):
            return reverse(parent[1])
        return reverse(parent) if parent else reverse("dashboard")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["heading"] = self.heading or self.get_breadcrumb_title()
        ctx["intro"] = self.intro
        ctx["back_url"] = self.get_back_url()
        ctx["submit_label"] = self.submit_label
        return ctx

    def form_valid(self, form):
        self.object = form.save()
        if self.success_message:
            messages.success(self.request, self.success_message)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(self.success_url_name)
