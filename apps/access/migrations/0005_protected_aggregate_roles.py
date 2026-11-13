from django.db import migrations


def update_roles(apps, schema_editor):
    permission_model = apps.get_model("access", "Permission")
    role_model = apps.get_model("access", "Role")
    role_permission_model = apps.get_model("access", "RolePermission")
    grant_model = apps.get_model("access", "GrantScope")
    permission, _ = permission_model.objects.get_or_create(
        code="dashboard.aggregate",
        defaults={"label": "View protected aggregate reports", "module": "dashboard"},
    )
    for role in role_model.objects.filter(code__in=["EXECUTIVE", "ANALYST"], is_system=True):
        role_permission, _ = role_permission_model.objects.update_or_create(
            role=role, permission=permission, defaults={"active": True}
        )
        grant_model.objects.update_or_create(
            role_permission=role_permission,
            scope_type="ORG_SUBTREE",
            service=None,
            organization=None,
            defaults={"active": True},
        )
        if role.code == "ANALYST":
            role_permission_model.objects.filter(
                role=role,
                permission__code__in=[
                    "request.view",
                    "incident.view",
                    "problem.view",
                    "metrics.view",
                    "metrics.export",
                ],
            ).delete()
        else:
            role_permission_model.objects.filter(
                role=role, permission__code__in=["metrics.view", "metrics.export"]
            ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0004_grantscope"),
    ]

    operations = [migrations.RunPython(update_roles, migrations.RunPython.noop)]
