"""Context processors shared by every page of the dashboard shell."""

from django.urls import NoReverseMatch, reverse

from .navigation import NAV_GROUPS, NAV_ITEMS


def _resolve(url_name: str) -> str | None:
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


def navigation(request):
    """Grouped sidebar items with a resolved URL and active-state flag.

    ``requires_staff`` items are hidden for non-staff users (apex semantics);
    platform sections use required-staff=False and enforce permissions in views.
    """
    is_staff = getattr(request.user, "is_staff", False)
    path = request.path

    groups = []
    for group_label in NAV_GROUPS:
        items = []
        for item in NAV_ITEMS:
            if item.group != group_label:
                continue
            if item.requires_staff and not is_staff:
                continue
            url = _resolve(item.url_name)
            items.append(
                {
                    "label": item.label,
                    "url": url or "#",
                    "icon": item.icon,
                    "current": bool(url and path == url),
                }
            )
        if items:
            groups.append({"label": group_label, "items": items})
    return {"nav_groups": groups}


def user_context(request):
    """Active organization memberships (org + service + role) for the user."""
    memberships = []
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and hasattr(user, "person"):
        person = user.person
        rows = (
            person.memberships.select_related("organization", "role")
            .filter(active=True)
            .order_by("organization__name", "role__name")
        )
        if rows:
            from apps.organizations.models import Organization
            from apps.services.models import ServiceActor

            visible_ids = set(
                Organization.objects.visible_to(user, "organization.view").values_list(
                    "pk", flat=True
                )
            )
            actors = ServiceActor.objects.filter(person=person).select_related("service")
            for m in rows:
                org = m.organization
                service = next(
                    (
                        a.service.name
                        for a in actors
                        if a.service.organization_id
                        in set(org.get_descendants(include_self=True).values_list("pk", flat=True))
                    ),
                    None,
                )
                memberships.append(
                    {
                        "pk": org.pk,
                        "organization": org.name,
                        "service": service,
                        "organization_type": org.get_organization_type_display(),
                        "role": m.role.name,
                        "url": f"/organization/{org.pk}/" if org.pk in visible_ids else None,
                    }
                )
    organizations = _distinct_organizations(memberships)
    active_organization = _active_organization_for_request(request, organizations)
    services = _visible_services_list(
        user, org_pk=active_organization["pk"] if active_organization else None
    )
    active_service = _active_service_for_request(request, services)
    return {
        "memberships": memberships,
        "organizations": organizations,
        "active_organization": active_organization,
        "services": services,
        "active_service": active_service,
    }


def _distinct_organizations(memberships):
    """One row per organization (a person may hold several roles at the same
    org); used by the header's single-select organization tab. Each entry
    carries the full location path (root → self), e.g. "Portugal / Lisboa /
    Torres Vedras", instead of the roles held there.
    """
    from apps.organizations.models import Organization

    seen, out = set(), []
    for m in memberships:
        if m["pk"] in seen:
            continue
        seen.add(m["pk"])
        out.append(
            {
                "pk": m["pk"],
                "organization": m["organization"],
                "organization_type": m["organization_type"],
                "url": m["url"],
                "path": m["organization"],
            }
        )
    if out:
        orgs = Organization.objects.filter(pk__in=seen).only("id", "path")
        by_pk = {o.pk: o for o in orgs}
        for entry in out:
            org = by_pk.get(entry["pk"])
            if org is not None:
                ancestors = org.get_ancestors(include_self=True).order_by("path")
                entry["path"] = " / ".join(a.name for a in ancestors)
    return out


def _active_organization_for_request(request, organizations):
    """Session-selected organization (single-select, header org tab)."""
    context = request.session.get("org_context") or {}
    pk = context.get("pk")
    if pk is None:
        return None
    return next((o for o in organizations if o["pk"] == pk), None)


def user_organization_pks(user):
    """Distinct organization pks the user holds an active membership at.

    Used to validate a session organization pick without re-deriving the
    full ``memberships``/``organizations`` context structures.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    person = getattr(user, "person", None)
    if person is None:
        return set()
    return set(
        person.memberships.filter(active=True).values_list("organization_id", flat=True)
    )


def _active_service_pk(path: str) -> int | None:
    """Service pk from ``/service/<pk>/`` or ``/services/<pk>/`` paths."""
    for prefix in ("/service/", "/services/"):
        if path.startswith(prefix):
            tail = path[len(prefix) :].rstrip("/")
            try:
                return int(tail.split("/", 1)[0])
            except ValueError:
                return None
    return None


def _active_service_from_path(path, services):
    pk = _active_service_pk(path)
    if pk is None:
        return None
    return next((s for s in services if s["pk"] == pk), None)


def _active_service_for_request(request, services):
    """Session-selected service+role, falling back to the path-highlight one."""
    context = request.session.get("service_context") or {}
    if context.get("pk") is not None:
        selected = next(
            (s for s in services if s["pk"] == context["pk"] and s["role_code"] == context.get("role")),
            None,
        )
        if selected is not None:
            return selected
    return _active_service_from_path(request.path, services)


def _visible_services_list(user, org_pk=None):
    """Service+role pairs the user holds a role on, for the selector.

    One entry per (service, granting role): matches the profile's Service
    roles. Each entry links to its dashboard only when the user may open it
    (``service.view``), so no dead links are offered. When ``org_pk`` is
    given, only memberships held at that organization are considered (the
    header scopes the service/role tab to the single selected organization).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    from apps.common.authorization import membership_service_scope
    from apps.services.models import Service

    person = getattr(user, "person", None)
    if person is None:
        return []
    openable = set(
        Service.objects.visible_to(user, "service.view")
        .alive()
        .filter(active=True)
        .values_list("pk", flat=True)
    )
    seen, out = set(), []
    rows = person.memberships.select_related("organization", "role").filter(active=True)
    if org_pk is not None:
        rows = rows.filter(organization_id=org_pk)
    for m in rows.order_by("organization__name", "role__name"):
        for svc in membership_service_scope(m):
            key = (svc.pk, m.role.code)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "pk": svc.pk,
                    "name": svc.name,
                    "organization": svc.organization.name,
                    "organization_type": svc.organization.get_organization_type_display(),
                    "role_name": m.role.name,
                    "role_code": m.role.code,
                    "url": f"/service/{svc.pk}/" if svc.pk in openable else None,
                }
            )
            if len(out) >= 120:
                return out
    return out
