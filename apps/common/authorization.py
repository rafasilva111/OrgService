"""Centralized authorization layer.

Goals
-----
* One place that decides "can this user do X to that resource?".
* DB-level filtering via ``QuerySet.visible_to(user, action)`` — never load
  records and filter in Python.
* Access flows DOWN the organization hierarchy (ltree), never up and never to
  siblings.
* Read/write permissions are separate atoms of the ``Permission`` model
  (e.g. ``request.view`` vs ``request.update``).

Data model (see ``apps.access``)
--------------------------------
    User ── Person ── OrganizationMembership ── Role ── RolePermission ── Permission
                          │                        └── ServiceAccess (optional)
                          ├── Organization (scope root of a subtree)
                          └── ServiceAccess (optional service restriction)

A membership grants a role's permissions *within the subtree rooted at the
membership's organization*.  If the membership has no ``ServiceAccess`` rows
it grants access to every service under that subtree; otherwise only to the
listed services.

Helper functions exposed for use by views/APIs:
    can(user, action, resource)                 -> bool
    visible(queryset, user, action="view")      -> QuerySet
"""

from django.db.models import Q


def default_permission_code(model, verb="view"):
    """Derive the conventional permission code from the model.

    e.g. ``Request`` → ``"request.view"``. Individual models may override by
    declaring ``_AUTH_PERMISSION_PREFIX`` (e.g. multi-table resources).
    """
    prefix = getattr(model, "_AUTH_PERMISSION_PREFIX", None) or model._meta.model_name
    return f"{prefix}.{verb}"


# ---------------------------------------------------------------------------
# Low-level scope builders (all queries stay in the database)
# ---------------------------------------------------------------------------


def _descendant_org_pks(org):
    """Subquery of every organization pk inside ``org``'s subtree (incl. self)."""
    from apps.organizations.models import Organization

    return Organization.objects.filter(Q(pk=org.pk) | Q(path__descendants=org.path)).values("pk")


def _permission_codes_for_role(role):
    """Codes granted by a role as a lazy query (no Python filtering of rows)."""
    from apps.access.models import Permission

    return Permission.objects.filter(
        rolepermissions__role=role, rolepermissions__active=True
    ).values_list("code", flat=True)


def _service_access_ids(membership):
    """Service ids explicitly allowed for a membership (empty => unrestricted)."""
    from apps.access.models import ServiceAccess

    return list(
        ServiceAccess.objects.filter(membership=membership, active=True).values_list(
            "service_id", flat=True
        )
    )


def membership_service_scope(membership):
    """Services a membership reaches.

    A membership with ``ServiceAccess`` rows is restricted to those services;
    one without rows reaches every service in its organization's subtree
    (see the ``ServiceAccess`` docstring).
    """
    from apps.services.models import Service

    ids = _service_access_ids(membership)
    if ids:
        qs = Service.objects.filter(pk__in=ids)
    else:
        qs = Service.objects.filter(
            organization_id__in=_descendant_org_pks(membership.organization)
        )
    return list(qs.alive().filter(active=True).order_by("name"))


def authorization_scope_q(model, user, action):
    """Build the OR'ed ``Q`` that selects the rows visible to ``user``.

    ``model`` must expose:
      * ``_AUTH_ORG_FIELD``     — attribute path to the organization FK
                                  (e.g. ``"organization"``) or ``"id"`` when
                                  the model *is* the organization;
      * ``_AUTH_SERVICE_FIELD`` — attribute path to the service FK or ``None``
                                  for organization-scoped resources only.

    Returns ``None`` when the user cannot see anything.
    """
    org_field = getattr(model, "_AUTH_ORG_FIELD", "organization")
    service_field = getattr(model, "_AUTH_SERVICE_FIELD", None)

    from apps.organizations.models import OrganizationMembership

    memberships = OrganizationMembership.objects.filter(
        person__user=user, active=True
    ).select_related("organization", "role")

    scopes = []
    for membership in memberships:
        if action not in _permission_codes_for_role(membership.role):
            continue

        if service_field:
            allowed = _service_access_ids(membership)
            if allowed:
                scopes.append(Q(**{f"{service_field}__in": allowed}))
                continue  # membership is restricted to these services

        if org_field:
            scopes.append(Q(**{f"{org_field}__in": _descendant_org_pks(membership.organization)}))

    if not scopes:
        return None
    query = scopes[0]
    for scope in scopes[1:]:
        query |= scope
    return query


# ---------------------------------------------------------------------------
# QuerySet / Manager mixins — use anywhere via ``objects.visible_to(user)``
# ---------------------------------------------------------------------------


class AuthorizationQuerySetMixin:
    def visible_to(self, user, action=None, verb="view"):
        """Rows the user is allowed to see for ``action`` (DB-level filter).

        ``action`` is a full permission code (``"request.view"``). When
        omitted it is derived from the model.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        action = action or default_permission_code(self.model, verb)
        scope = authorization_scope_q(self.model, user, action)
        return self.filter(scope) if scope is not None else self.none()


class AuthorizationManagerMixin:
    def __init__(self, *args, **kwargs):
        model = kwargs.pop("_model", None)
        super().__init__(*args, **kwargs)
        if model is not None:
            self.model = model

    def get_queryset(self):
        qs = super().get_queryset()
        qs.visible_to = AuthorizationQuerySetMixin.visible_to.__get__(qs, type(qs))
        return qs


def AuthorizationManager(model=None, qs_class=None):
    """Factory returning a manager whose queryset exposes ``visible_to``."""
    from django.db import models

    base_qs = qs_class or models.QuerySet

    class _AuthQuerySet(base_qs):
        def visible_to(self, user, action="view"):
            return AuthorizationQuerySetMixin.visible_to(self, user, action)

    class _AuthManager(models.Manager.from_queryset(_AuthQuerySet)):
        pass

    manager = _AuthManager()
    if model is not None:
        manager.model = model
    return manager


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------


def can(user, action, resource):
    """Boolean check: can ``user`` perform ``action`` on ``resource``?

    ``resource`` may be a model instance, or a model class for type-level
    checks (short-circuited through ``visible_to`` on an empty queryset is not
    meaningful for classes, so instance checks are the supported path).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    model = resource.__class__
    if getattr(model, "_AUTH_ORG_FIELD", "organization") == "id":
        # Organization itself: is the org inside one of the user's subtrees?
        scope = authorization_scope_q(model, user, action)
        if scope is None:
            return False
        return resource.__class__.objects.filter(pk=resource.pk).filter(scope).exists()
    return resource.__class__.objects.filter(pk=resource.pk).visible_to(user, action).exists()


def visible(queryset, user, action=None, verb="view"):
    """Alias: ``visible(Request.objects.all(), user)`` → filtered queryset."""
    return queryset.visible_to(user, action=action, verb=verb)
