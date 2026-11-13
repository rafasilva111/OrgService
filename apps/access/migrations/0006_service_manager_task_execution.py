from django.db import migrations


def grant_service_manager_task_execution(apps, schema_editor):
    Permission = apps.get_model("access", "Permission")
    Role = apps.get_model("access", "Role")
    RolePermission = apps.get_model("access", "RolePermission")
    GrantScope = apps.get_model("access", "GrantScope")
    permission, _ = Permission.objects.get_or_create(
        code="offering.execute",
        defaults={"label": "Execute offering", "module": "offerings"},
    )
    roles = Role.objects.filter(is_system=True).filter(
        code="SERVICE_MANAGER"
    ) | Role.objects.filter(is_system=True, code__startswith="SERVICE_MANAGER_")
    for role in roles:
        scopes = list(
            GrantScope.objects.filter(
                role_permission__role=role,
                role_permission__permission__code="service.view",
                role_permission__active=True,
                active=True,
                scope_type="SPECIFIC_SERVICE",
                service__isnull=False,
            )
        )
        if role.code != "SERVICE_MANAGER" and not scopes:
            continue
        grant, created = RolePermission.objects.get_or_create(role=role, permission=permission)
        if not created:
            continue
        if role.code != "SERVICE_MANAGER":
            for scope in scopes:
                GrantScope.objects.get_or_create(
                    role_permission=grant,
                    scope_type=scope.scope_type,
                    service_id=scope.service_id,
                    organization_id=scope.organization_id,
                )


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0005_protected_aggregate_roles"),
    ]

    operations = [
        migrations.RunPython(grant_service_manager_task_execution, migrations.RunPython.noop),
    ]
