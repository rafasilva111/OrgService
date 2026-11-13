"""Helpers for creating scoped role grants.

The seed commands use these helpers so repeated runs stay idempotent and do not
delete existing ``GrantScope`` rows through cascading role-permission rebuilds.
"""

from django.db import transaction

from apps.access.models import (
    GrantScope,
    GrantScopeType,
    Role,
    RolePermission,
)

SERVICE_MANAGER_CODE = "SERVICE_MANAGER"

ORG_WIDE_ROLE_CODES = (
    "EXECUTIVE",
    "OPERATIONS_MANAGER",
    "OPERATOR",
    "REQUEST_MANAGER",
    "INCIDENT_MANAGER",
    "PROBLEM_MANAGER",
    "ANALYST",
    "SUPPLIES_MANAGER",
)


def ensure_role_permission(role, permission):
    role_permission, _ = RolePermission.objects.update_or_create(
        role=role,
        permission=permission,
        defaults={"active": True},
    )
    return role_permission


def ensure_grant_scope(role_permission, scope_type, *, service=None, organization=None):
    scope, _ = GrantScope.objects.update_or_create(
        role_permission=role_permission,
        scope_type=scope_type,
        service=service,
        organization=organization,
        defaults={"active": True},
    )
    return scope


def service_role_code(service, key=None):
    """Stable role code for a service-scoped service-manager role."""
    if key:
        return f"{SERVICE_MANAGER_CODE}_{key}"[:64]
    return f"{SERVICE_MANAGER_CODE}_S{service.pk}"


@transaction.atomic
def ensure_service_manager_role(base_role, service, *, key=None):
    """Clone the base service-manager role for one service.

    Every cloned permission gets an explicit ``SPECIFIC_SERVICE`` scope. The
    base role remains an unscoped template.
    """
    code = service_role_code(service, key=key)
    role, _ = Role.objects.update_or_create(
        code=code,
        defaults={
            "name": f"{base_role.name} — {service.name}"[:128],
            "is_system": True,
            "active": True,
            "deleted_at": None,
        },
    )

    base_permissions = list(
        base_role.rolepermissions.filter(active=True).select_related("permission")
    )
    for base_rp in base_permissions:
        role_permission = ensure_role_permission(role, base_rp.permission)
        ensure_grant_scope(
            role_permission,
            GrantScopeType.SPECIFIC_SERVICE,
            service=service,
        )

    return role


@transaction.atomic
def ensure_org_wide_scopes(role, permission_codes=None):
    """Give every permission of an org-wide role the membership subtree scope."""
    permissions = role.rolepermissions.filter(active=True)
    if permission_codes is not None:
        permissions = permissions.filter(permission__code__in=permission_codes)
    for role_permission in permissions:
        if not role_permission.grant_scopes.exists():
            ensure_grant_scope(role_permission, GrantScopeType.ORG_SUBTREE)
