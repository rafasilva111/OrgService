"""Citizen portal ("Portal do Munícipe") — mounted at ``/citizen/``.

Authorization differs from the staff side on purpose (ADR 019):

* the catalogue is public to every signed-in citizen (see ``selectors``);
* a citizen may create a request for any public offering;
* a citizen only sees requests where they are the requester, and only their
  Comments and the public execution progress — never Work Notes.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View

from apps.offerings.models import (
    ExecutionNote,
    FormFieldType,
    OfferingExecution,
    ServiceOfferingType,
    TaskActivity,
)
from apps.offerings.request_form import answer_rows, answers_form_for
from apps.organizations.models import Organization
from apps.people.models import Person, user_display_name
from apps.requests.models import Request, RequestAttachment, RequestStatus

from . import selectors
from .forms import (
    CitizenAuthenticationForm,
    CitizenCommentForm,
    CitizenProfileForm,
    CitizenRequestForm,
    CitizenSignupForm,
)

PUBLIC_ACTIVITY_ACTIONS = ("created", "started", "completed", "failed", "paused", "resumed")


def citizen_person(user):
    return (
        Person.objects.filter(user=user, active=True, deleted_at__isnull=True)
        .select_related("home_organization")
        .first()
        if user.is_authenticated
        else None
    )


def _int_or_none(value):
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


class CitizenMixin(LoginRequiredMixin):
    login_url = reverse_lazy("citizen:login")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["citizen"] = citizen_person(self.request.user)
        return ctx


# ----------------------------------------------------------------- account
class CitizenLoginView(LoginView):
    template_name = "citizen/login.html"
    authentication_form = CitizenAuthenticationForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        return reverse("citizen:home")


class CitizenLogoutView(LogoutView):
    next_page = reverse_lazy("citizen:login")


class CitizenSignupView(FormView):
    template_name = "citizen/signup.html"
    form_class = CitizenSignupForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("citizen:home")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, _("Welcome! Your account was created."))
        return redirect("citizen:home")


class CitizenProfileView(CitizenMixin, FormView):
    template_name = "citizen/profile.html"
    form_class = CitizenProfileForm

    def dispatch(self, request, *args, **kwargs):
        self.person = citizen_person(request.user) if request.user.is_authenticated else None
        if request.user.is_authenticated and self.person is None:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["person"] = self.person
        return kwargs

    def get_initial(self):
        from apps.profiles.models import UserProfile

        profile = UserProfile.objects.filter(user=self.request.user).first()
        return {
            "full_name": self.person.full_name,
            "email": self.person.email or self.request.user.email,
            "phone": self.person.phone or "",
            "home_organization": self.person.home_organization_id,
            "language": profile.language if profile else "",
        }

    @transaction.atomic
    def form_valid(self, form):
        from apps.profiles.models import UserProfile

        data = form.cleaned_data
        self.person.full_name = data["full_name"]
        self.person.email = data["email"]
        self.person.phone = data["phone"] or None
        self.person.home_organization = data["home_organization"]
        self.person.save(
            update_fields=["full_name", "email", "phone", "home_organization", "updated_at"]
        )
        user = self.request.user
        user.email = data["email"]
        user.save(update_fields=["email"])
        profile, _created = UserProfile.objects.get_or_create(user=user)
        profile.language = data["language"]
        profile.save(update_fields=["language", "updated_at"])
        if data["language"]:
            translation.activate(data["language"])
        messages.success(self.request, _("Profile saved."))
        return redirect("citizen:profile")


# ---------------------------------------------------------------- browsing
class CitizenHomeView(CitizenMixin, TemplateView):
    template_name = "citizen/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        mine = selectors.citizen_requests(self.request.user)
        ctx["open_requests"] = mine.exclude(status__in=selectors.CLOSED_STATUSES).order_by(
            "-created_at"
        )[:3]
        ctx["counts"] = selectors.request_counts(self.request.user)
        ctx["disrupted_services"] = selectors.disrupted_services()
        ctx["popular_offerings"] = selectors.public_offerings()[:6]
        return ctx


class CitizenServiceListView(CitizenMixin, TemplateView):
    template_name = "citizen/services.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        organization = _int_or_none(self.request.GET.get("organization"))
        offering_type = _int_or_none(self.request.GET.get("type"))
        services = selectors.catalogue_services(
            query=query, organization=organization, offering_type=offering_type
        )
        groups = {}
        for service in services:
            groups.setdefault(service.organization, []).append(service)
        ctx.update(
            {
                "groups": list(groups.items()),
                "query": query,
                "selected_organization": organization,
                "selected_type": offering_type,
                "organizations": Organization.objects.filter(
                    pk__in=selectors.public_services().values("organization_id")
                ).order_by("path"),
                "offering_types": ServiceOfferingType.objects.filter(
                    pk__in=selectors.public_offerings().values("offering_type_id")
                ).order_by("name"),
            }
        )
        return ctx


class CitizenServiceDetailView(CitizenMixin, TemplateView):
    template_name = "citizen/service_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        service = get_object_or_404(selectors.public_services(), pk=kwargs["pk"])
        ctx["service"] = service
        ctx["offerings"] = selectors.public_offerings().filter(service=service)
        return ctx


class CitizenOfferingDetailView(CitizenMixin, TemplateView):
    template_name = "citizen/offering_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        offering = get_object_or_404(selectors.public_offerings(), pk=kwargs["pk"])
        ctx["offering"] = offering
        ctx["steps"] = selectors.public_steps(offering)
        ctx["questions"] = list(offering.form_fields.order_by("order"))
        ctx["documents"] = [q for q in ctx["questions"] if q.field_type == FormFieldType.FILE]
        return ctx


# ---------------------------------------------------------------- requests
class CitizenRequestCreateView(CitizenMixin, FormView):
    template_name = "citizen/request_form.html"
    form_class = CitizenRequestForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        pk = _int_or_none(request.GET.get("offering") or request.POST.get("offering"))
        if pk is None:
            return redirect("citizen:services")
        self.offering = get_object_or_404(selectors.public_offerings(), pk=pk)
        self.person = citizen_person(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"title": self.offering.name}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["offering"] = self.offering
        ctx["answers_form"] = getattr(self, "answers_form", None) or answers_form_for(self.offering)
        return ctx

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        self.answers_form = answers_form_for(self.offering, request.POST, request.FILES)
        answers_ok = self.answers_form.is_valid()
        if form.is_valid() and answers_ok:
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form):
        if self.person is None:
            form.add_error(None, _("Your account is not linked to a citizen profile."))
            return self.form_invalid(form)
        try:
            request_obj = create_citizen_request(
                self.person,
                self.offering,
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                answers_form=self.answers_form,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Your request was submitted."))
        return redirect("citizen:request_detail", pk=request_obj.pk)


@transaction.atomic
def create_citizen_request(person, offering, *, title, description, answers_form):
    """Save a request on ``offering`` for ``person`` and start its execution.

    Mirrors the staff ``RequestCreateView.form_valid``: priority comes from
    the offering, answers go to ``form_data``, files to ``RequestAttachment``.
    """
    from apps.offerings.execution import start_request_execution

    data, uploads = answers_form.answers()
    request_obj = Request(
        service=offering.service,
        offering=offering,
        title=title,
        description=description,
        requester=person,
        created_by=person,
        status=RequestStatus.NEW,
        priority=offering.default_priority,
        form_data=data,
    )
    request_obj.save()
    start_request_execution(request_obj, person)
    for key, upload in uploads:
        attachment = RequestAttachment(
            request=request_obj,
            field_key=key,
            original_name=upload.name[:255],
            size=upload.size,
            content_type=(getattr(upload, "content_type", "") or "")[:128],
        )
        attachment.file.save(upload.name, upload, save=True)
        request_obj.form_data[key] = {"attachment": attachment.pk, "name": attachment.original_name}
    if uploads:
        request_obj.save(update_fields=["form_data", "updated_at"])
    return request_obj


class CitizenRequestListView(CitizenMixin, TemplateView):
    template_name = "citizen/requests.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        state = self.request.GET.get("state", "open")
        qs = selectors.citizen_requests(self.request.user).order_by("-created_at")
        if state == "open":
            qs = qs.exclude(status__in=selectors.CLOSED_STATUSES)
        elif state == "closed":
            qs = qs.filter(status__in=selectors.CLOSED_STATUSES)
        else:
            state = "all"
        ctx["requests"] = qs
        ctx["state"] = state
        ctx["counts"] = selectors.request_counts(self.request.user)
        return ctx


def request_execution(request_obj):
    return (
        OfferingExecution.objects.filter(
            subject_type=ContentType.objects.get_for_model(Request), subject_id=request_obj.pk
        )
        .select_related("current_step")
        .first()
    )


def citizen_timeline(request_obj, execution, viewer):
    """Comments and public progress, newest first. Work Notes never appear;
    staff names are not shown on progress entries."""
    entries = []
    for comment in request_obj.comments.select_related("author"):
        entries.append(
            {
                "kind": "message",
                "created_at": comment.created_at,
                "author": comment.author.full_name if comment.author else "—",
                "mine": bool(comment.author and comment.author.user_id == viewer.pk),
                "body": comment.body,
            }
        )
    if execution is not None:
        notes = execution.notes.filter(kind=ExecutionNote.Kind.COMMENT).select_related(
            "author", "author__person"
        )
        for note in notes:
            entries.append(
                {
                    "kind": "message",
                    "created_at": note.created_at,
                    "author": user_display_name(note.author),
                    "mine": note.author_id == viewer.pk,
                    "body": note.body,
                }
            )
        activities = TaskActivity.objects.filter(
            task__execution=execution, action__in=PUBLIC_ACTIVITY_ACTIONS
        ).select_related("task__step")
        for activity in activities:
            entries.append(
                {
                    "kind": "progress",
                    "created_at": activity.created_at,
                    "action": activity.action,
                    "label": activity.action_label,
                    "step": activity.task.step.name,
                }
            )
    return sorted(entries, key=lambda e: e["created_at"], reverse=True)


class CitizenRequestDetailView(CitizenMixin, TemplateView):
    template_name = "citizen/request_detail.html"

    def get_object(self):
        return get_object_or_404(
            selectors.citizen_requests(self.request.user), pk=self.kwargs["pk"]
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        req = getattr(self, "object", None) or self.get_object()
        execution = request_execution(req)
        ctx.update(
            {
                "req": req,
                "execution": execution,
                "answers": answer_rows(req),
                "timeline": citizen_timeline(req, execution, self.request.user),
                "is_open": req.status not in selectors.CLOSED_STATUSES,
                "comment_form": kwargs.get("comment_form") or CitizenCommentForm(),
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.status in selectors.CLOSED_STATUSES:
            messages.error(request, _("This request is closed."))
            return redirect("citizen:request_detail", pk=self.object.pk)
        form = CitizenCommentForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(comment_form=form), status=400)
        execution = request_execution(self.object)
        if execution is not None:
            from apps.offerings.views import add_execution_note

            add_execution_note(
                execution,
                request.user,
                form.cleaned_data["body"],
                kind=ExecutionNote.Kind.COMMENT,
                subject=self.object,
            )
        else:
            self.object.comments.create(
                author=citizen_person(request.user), body=form.cleaned_data["body"]
            )
        messages.success(request, _("Message sent."))
        return redirect(f"{reverse('citizen:request_detail', args=[self.object.pk])}#timeline")


class CitizenAttachmentView(CitizenMixin, View):
    """Download an attachment of one of the citizen's own requests (404 otherwise)."""

    def get(self, request, pk, attachment_pk):
        attachment = RequestAttachment.objects.filter(
            pk=attachment_pk,
            request_id=pk,
            request__in=selectors.citizen_requests(request.user),
        ).first()
        if attachment is None or not attachment.file:
            raise Http404
        try:
            handle = attachment.file.open("rb")
        except FileNotFoundError as exc:
            raise Http404 from exc
        return FileResponse(handle, as_attachment=True, filename=attachment.original_name)
