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


def _service_ids_below_service(service):
    """Subquery of service pks under ``service`` (incl. itself)."""
    from apps.services.models import Service

    return Service.objects.filter(path__descendants=service.path).values("pk")


def _service_ids_below_org(org):
    """Subquery of service pks under ``org``'s subtree (incl. itself)."""
    from apps.services.models import Service

    return Service.objects.filter(organization_id__in=_descendant_org_pks(org)).values("pk")


def _org_ids_for_services(service_ids):
    """Subquery of organization pks owning the given services."""
    from apps.services.models import Service

    return Service.objects.filter(pk__in=service_ids).values("organization_id")


def _role_permissions_for_membership(membership, action=None):
    """Active role permissions for a membership, optionally filtered by code."""
    from apps.access.models import RolePermission

    qs = RolePermission.objects.filter(role=membership.role, active=True)
    if action is not None:
        qs = qs.filter(permission__code=action)
    return list(qs.select_related("permission").prefetch_related("grant_scopes"))


def _inherit_scope_q(membership, service_field, org_field):
    """Current (pre-GrantScope) behavior for a membership."""
    if service_field:
        allowed = _service_access_ids(membership)
        if allowed:
            return Q(**{f"{service_field}__in": allowed})
    if org_field:
        return Q(**{f"{org_field}__in": _descendant_org_pks(membership.organization)})
    return None


def _intersect_service_access(query, lookup, allowed):
    if allowed:
        return query & Q(**{lookup: allowed})
    return query


def _service_scope_q(membership, scope, service_field, allowed):
    """Build the service-field ``Q`` for one grant scope."""
    from apps.access.models import GrantScopeType
    from apps.organizations.models import Organization
    from apps.services.models import Service

    lookup = f"{service_field}__in"
    if scope is None or scope.scope_type == GrantScopeType.INHERIT:
        if allowed:
            return Q(**{lookup: allowed})
        return Q(**{lookup: _service_ids_below_org(membership.organization)})

    if scope.scope_type == GrantScopeType.ALL_SERVICES:
        return Q(**{lookup: allowed}) if allowed else Q()

    if scope.scope_type == GrantScopeType.SPECIFIC_SERVICE:
        if scope.service_id is None:
            return None
        if allowed and scope.service_id not in allowed:
            return None
        return Q(**{lookup: [scope.service_id]})

    if scope.scope_type == GrantScopeType.ORG_SUBTREE:
        query = Q(**{lookup: _service_ids_below_org(membership.organization)})
        return _intersect_service_access(query, lookup, allowed)

    if scope.scope_type == GrantScopeType.BELOW_SERVICES:
        if scope.service_id is not None:
            anchor = Service.objects.filter(pk=scope.service_id).only("path").first()
            if anchor is None:
                return None
            query = Q(**{lookup: _service_ids_below_service(anchor)})
        elif scope.organization_id is not None:
            anchor = Organization.objects.filter(pk=scope.organization_id).only("path").first()
            if anchor is None:
                return None
            query = Q(**{lookup: _service_ids_below_org(anchor)})
        else:
            return None
        return _intersect_service_access(query, lookup, allowed)

    return None


def _org_scope_q(membership, scope, org_field):
    """Build the organization-field ``Q`` for one grant scope."""
    from apps.access.models import GrantScopeType
    from apps.organizations.models import Organization
    from apps.services.models import Service

    lookup = f"{org_field}__in"
    if scope is None or scope.scope_type == GrantScopeType.INHERIT:
        return Q(**{lookup: _descendant_org_pks(membership.organization)})

    if scope.scope_type == GrantScopeType.ALL_SERVICES:
        return Q()

    if scope.scope_type == GrantScopeType.ORG_SUBTREE:
        return Q(**{lookup: _descendant_org_pks(membership.organization)})

    if scope.scope_type == GrantScopeType.SPECIFIC_SERVICE:
        if scope.service_id is None:
            return None
        org_id = (
            Service.objects.filter(pk=scope.service_id)
            .values_list("organization_id", flat=True)
            .first()
        )
        if org_id is None:
            return None
        return Q(**{lookup: [org_id]})

    if scope.scope_type == GrantScopeType.BELOW_SERVICES:
        if scope.organization_id is not None:
            anchor = Organization.objects.filter(pk=scope.organization_id).only("path").first()
            if anchor is None:
                return None
            return Q(**{lookup: _descendant_org_pks(anchor)})
        if scope.service_id is not None:
            anchor = Service.objects.filter(pk=scope.service_id).only("path").first()
            if anchor is None:
                return None
            return Q(**{lookup: _org_ids_for_services(_service_ids_below_service(anchor))})

    return None


def _scope_q_for_membership(membership, scope, service_field, org_field):
    if service_field:
        return _service_scope_q(membership, scope, service_field, _service_access_ids(membership))
    if org_field:
        return _org_scope_q(membership, scope, org_field)
    return None


def _service_ids_for_scope(membership, scope):
    """Concrete service ids reached by one scope (used by the header selector)."""
    from apps.access.models import GrantScopeType
    from apps.organizations.models import Organization
    from apps.services.models import Service

    if scope is None or scope.scope_type == GrantScopeType.INHERIT:
        allowed = _service_access_ids(membership)
        if allowed:
            return set(allowed)
        return set(
            Service.objects.filter(
                organization_id__in=_descendant_org_pks(membership.organization)
            ).values_list("pk", flat=True)
        )

    if scope.scope_type == GrantScopeType.ALL_SERVICES:
        return set(Service.objects.values_list("pk", flat=True))

    if scope.scope_type == GrantScopeType.ORG_SUBTREE:
        return set(
            Service.objects.filter(
                organization_id__in=_descendant_org_pks(membership.organization)
            ).values_list("pk", flat=True)
        )

    if scope.scope_type == GrantScopeType.SPECIFIC_SERVICE:
        return {scope.service_id} if scope.service_id else set()

    if scope.scope_type == GrantScopeType.BELOW_SERVICES:
        if scope.service_id is not None:
            anchor = Service.objects.filter(pk=scope.service_id).only("path").first()
            if anchor is not None:
                return set(
                    Service.objects.filter(path__descendants=anchor.path).values_list(
                        "pk", flat=True
                    )
                )
        elif scope.organization_id is not None:
            anchor = Organization.objects.filter(pk=scope.organization_id).only("path").first()
            if anchor is not None:
                return set(
                    Service.objects.filter(
                        organization_id__in=_descendant_org_pks(anchor)
                    ).values_list("pk", flat=True)
                )

    return set()


def membership_service_scope(membership):
    """Services a membership reaches, honoring explicit ``GrantScope`` rows.

    No ``service.view`` scope (or no ``service.view`` permission) keeps the
    legacy behavior: ``ServiceAccess`` if present, else the organization
    subtree.
    """
    from apps.services.models import Service

    role_permissions = _role_permissions_for_membership(membership, "service.view")
    ids = set()
    has_scope = False
    for role_permission in role_permissions:
        active_scopes = [s for s in role_permission.grant_scopes.all() if s.active]
        if active_scopes:
            has_scope = True
            for scope in active_scopes:
                ids |= _service_ids_for_scope(membership, scope)

    if not has_scope:
        ids = _service_ids_for_scope(membership, None)

    allowed = _service_access_ids(membership)
    if allowed:
        ids &= set(allowed)

    if not ids:
        return []
    return list(Service.objects.filter(pk__in=ids).alive().filter(active=True).order_by("name"))


def authorization_scope_q(model, user, action=None):
    """Build the OR'ed ``Q`` that selects the rows visible to ``user``.

    ``model`` must expose:
      * ``_AUTH_ORG_FIELD``     — attribute path to the organization FK
                                  (e.g. ``"organization"``) or ``"id"`` when
                                  the model *is* the organization;
      * ``_AUTH_SERVICE_FIELD`` — attribute path to the service FK or ``None``
                                  for organization-scoped resources only.

    ``action=None`` means "any permission" and is used by organization-scope
    helpers that need the union of all memberships.

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
        role_permissions = _role_permissions_for_membership(membership, action)
        if not role_permissions:
            if action is None:
                query = _scope_q_for_membership(membership, None, service_field, org_field)
                if query is not None:
                    scopes.append(query)
            continue

        for role_permission in role_permissions:
            active_scopes = [s for s in role_permission.grant_scopes.all() if s.active]
            if not active_scopes:
                query = _scope_q_for_membership(membership, None, service_field, org_field)
                if query is not None:
                    scopes.append(query)
                continue
            for scope in active_scopes:
                query = _scope_q_for_membership(membership, scope, service_field, org_field)
                if query is not None:
                    scopes.append(query)

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
