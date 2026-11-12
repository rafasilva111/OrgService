"""View mixins & helpers tying views to the platform authorization layer.

Two complementary mechanisms:

1. ``user_has_permission_code`` — flat "can any of my memberships reach this
   permission code somewhere?" used to gate *create/write* actions and pages
   whose content is global (roles, permissions, skills).

2. DB-level scoping via ``Model.objects.visible_to(user, action)`` — used by
   the list/detail views so every page shows exactly the rows the user may see.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.views.generic import DetailView, ListView, View


def user_has_permission_code(user, code: str) -> bool:
    from apps.organizations.models import OrganizationMembership

    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return OrganizationMembership.objects.filter(
        active=True,
        person__user=user,
        role__rolepermissions__permission__code=code,
        role__rolepermissions__active=True,
    ).exists()


def user_has_any_permission(user, codes) -> bool:
    return any(user_has_permission_code(user, code) for code in codes or ())


def user_has_scoped_permission(user, code: str, *, service=None, organization=None) -> bool:
    """Can the user perform ``code`` on this service or organization?

    Unlike ``user_has_permission_code`` this applies the membership's
    ``GrantScope``/``ServiceAccess`` reach. It is the write-side counterpart of
    ``Model.objects.visible_to``.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    if service is not None:
        from apps.services.models import Service

        pk = getattr(service, "pk", service)
        return Service.objects.visible_to(user, code).filter(pk=pk).exists()
    if organization is not None:
        from apps.organizations.models import Organization

        pk = getattr(organization, "pk", organization)
        return Organization.objects.visible_to(user, code).filter(pk=pk).exists()
    return user_has_permission_code(user, code)


class PermissionRequiredMixin(LoginRequiredMixin, View):
    """Login + any-of-``platform_codes`` gate.

    Anonymous users are redirected to ``LOGIN_URL`` (with ``?next=``); logged-in
    users without a matching permission receive a 403.
    """

    requires_login: bool = True
    platform_codes: tuple[str, ...] = ()

    def get_platform_codes(self) -> tuple[str, ...]:
        return tuple(self.platform_codes)

    def dispatch(self, request, *args, **kwargs):
        if self.requires_login and not request.user.is_authenticated:
            return self.handle_no_permission()
        if not user_has_any_permission(request.user, self.get_platform_codes()):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ScopedListView(PermissionRequiredMixin, ListView):
    """List view scoped by ``scope_action`` (a full permission code)."""

    scope_action: str = ""

    def get_platform_codes(self) -> tuple[str, ...]:
        return (self.scope_action,) if self.scope_action else super().get_platform_codes()


class ScopedDetailView(PermissionRequiredMixin, DetailView):
    """Detail view: rows pre-scoped via ``visible_to`` so out-of-scope → 404."""

    scope_action: str = ""

    def get_platform_codes(self) -> tuple[str, ...]:
        return (self.scope_action,) if self.scope_action else super().get_platform_codes()

    def get_queryset(self):
        qs = super().get_queryset()
        model = self.model
        has_auth_field = (
            getattr(model, "_AUTH_ORG_FIELD", None) is not None
            or getattr(model, "_AUTH_SERVICE_FIELD", None) is not None
        )
        if has_auth_field:
            qs = qs.visible_to(self.request.user, action=self.scope_action)
        return qs


class CreatePermissionMixin(LoginRequiredMixin, View):
    """Login + ``create_perm_code`` gate (write actions)."""

    create_perm_code: str = ""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.create_perm_code and not user_has_permission_code(
            request.user, self.create_perm_code
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def visible_services(user):
    """Services the user may see — source of truth for custom-scoped queries."""
    from apps.services.models import Service

    return Service.objects.visible_to(user, action="service.view")


def service_filter_choices(view):
    """Dynamic (pk, name) choices for a table's "Service" dropdown filter.

    Pass directly as a ``Filter.choices_callable`` — scopes options to what
    the requesting user may see, matching the free-text filter it replaces.
    """
    return [
        (str(s.pk), s.name)
        for s in visible_services(view.request.user).order_by("name").only("id", "name")
    ]


def visible_orgs(user):
    """Organizations the user may see — source of truth for custom-scoped queries."""
    from apps.organizations.models import Organization

    return Organization.objects.visible_to(user, action="organization.view")


def selected_service_pk(user, session) -> int | None:
    """Service pk from the header's Service/Role pick, if still visible.

    The session value is validated against the same authorization scope the
    lists use, so a stale (or manipulated) pk can never widen access.
    """
    context = (session or {}).get("service_context") or {}
    pk = context.get("pk")
    if pk is None:
        return None
    return visible_services(user).alive().filter(pk=pk).values_list("pk", flat=True).first()


def selected_organization_pk(user, session) -> int | None:
    """Organization pk from the header's organization pick, if still visible."""
    context = (session or {}).get("org_context") or {}
    pk = context.get("pk")
    if pk is None:
        return None
    return visible_orgs(user).alive().filter(pk=pk).values_list("pk", flat=True).first()


def org_filter_choices(view):
    """Dynamic (pk, name) choices for a table's "Parent organization" dropdown.

    Pass directly as a ``Filter.choices_callable`` — scopes options to what the
    requesting user may see, matching the authorization scope applied to the rows
    themselves. Ordered by ``path`` so the list reads top-down through the
    hierarchy.
    """
    return [
        (str(o.pk), o.name)
        for o in visible_orgs(view.request.user).alive().order_by("path").only("id", "name")
    ]


def visible_org_ids(user):
    """Organization ids the user may see (empty means superuser sees all).

    Mirrors ``Service.objects.visible_to`` semantics: unrestricted memberships
    contribute their whole organizational subtree; service-restricted
    memberships contribute only the organizations of their allowed services.
    The union now comes from ``GrantScope`` when present, falling back to the
    legacy membership behavior when no scope exists.
    """
    from apps.common.authorization import authorization_scope_q
    from apps.organizations.models import Organization

    if getattr(user, "is_superuser", False):
        return []

    scope = authorization_scope_q(Organization, user, None)
    if scope is None:
        return []
    return list(Organization.objects.filter(scope).values_list("pk", flat=True).distinct())
