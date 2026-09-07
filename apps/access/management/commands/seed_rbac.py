"""Seed the default RBAC catalogue (permissions + roles).

Usage: ``python manage.py seed_rbac``
Idempotent: existing codes are updated, missing ones created.
"""

from django.core.management.base import BaseCommand

from apps.access.models import Permission, Role, RolePermission

PERMISSIONS = [
    ("service.view", "View service", "services"),
    ("service.manage", "Manage service", "services"),
    ("request.view", "View request", "requests"),
    ("request.create", "Create request", "requests"),
    ("request.update", "Update request", "requests"),
    ("request.assign", "Assign request", "requests"),
    ("request.close", "Close request", "requests"),
    ("incident.view", "View incident", "incidents"),
    ("incident.create", "Create incident", "incidents"),
    ("incident.assign", "Assign incident", "incidents"),
    ("incident.resolve", "Resolve incident", "incidents"),
    ("problem.view", "View problem", "problems"),
    ("problem.manage", "Manage problem", "problems"),
    ("workflow.view", "View workflow", "workflows"),
    ("workflow.execute", "Execute workflow", "workflows"),
    ("workflow.manage", "Manage workflow", "workflows"),
    ("resource.view", "View resource", "resources"),
    ("resource.manage", "Manage resource", "resources"),
    ("metrics.view", "View metrics", "metrics"),
    ("metrics.export", "Export metrics", "metrics"),
    ("change.view", "View change", "changes"),
    ("change.approve", "Approve change", "changes"),
    ("change.manage", "Manage change", "changes"),
    ("people.view", "View people", "people"),
    ("people.manage", "Manage people", "people"),
    ("event.view", "View events", "events"),
    ("audit.view", "View audit log", "audit"),
    ("organization.view", "View organization", "organizations"),
    ("organization.manage", "Manage organization", "organizations"),
    ("organization.create", "Create organization", "organizations"),
    ("organization.delete", "Delete organization", "organizations"),
    ("service.create", "Create service", "services"),
    ("service.delete", "Delete service", "services"),
    ("people.create", "Create person", "people"),
    ("people.delete", "Delete person", "people"),
    ("resource.create", "Create resource", "resources"),
    ("resource.delete", "Delete resource", "resources"),
    ("role.view", "View role", "access"),
    ("role.create", "Create role", "access"),
    ("role.manage", "Manage role", "access"),
    ("role.delete", "Delete role", "access"),
]

ROLES = {
    "EXECUTIVE": [
        "organization.view",
        "organization.manage",
        "organization.create",
        "organization.delete",
        "service.view",
        "service.create",
        "service.delete",
        "people.view",
        "people.manage",
        "people.create",
        "people.delete",
        "metrics.view",
        "metrics.export",
        "change.approve",
        "audit.view",
        "role.view",
        "role.create",
        "role.manage",
        "role.delete",
    ],
    "SERVICE_MANAGER": [
        "organization.view",
        "service.view",
        "service.manage",
        "service.create",
        "service.delete",
        "request.view",
        "request.create",
        "request.update",
        "request.assign",
        "request.close",
        "incident.view",
        "incident.create",
        "incident.assign",
        "incident.resolve",
        "problem.view",
        "problem.manage",
        "workflow.view",
        "workflow.manage",
        "resource.view",
        "resource.manage",
        "metrics.view",
        "metrics.export",
        "change.view",
        "change.manage",
        "event.view",
    ],
    "OPERATIONS_MANAGER": [
        "organization.view",
        "service.view",
        "request.view",
        "request.assign",
        "request.close",
        "incident.view",
        "incident.assign",
        "incident.resolve",
        "problem.manage",
        "workflow.view",
        "workflow.execute",
        "resource.manage",
        "resource.create",
        "resource.delete",
        "change.manage",
        "metrics.view",
    ],
    "OPERATOR": [
        "request.view",
        "request.update",
        "incident.view",
        "incident.create",
        "workflow.view",
        "workflow.execute",
        "resource.view",
    ],
    "REQUEST_MANAGER": [
        "request.view",
        "request.create",
        "request.update",
        "request.assign",
        "request.close",
        "workflow.view",
        "workflow.execute",
    ],
    "INCIDENT_MANAGER": [
        "incident.view",
        "incident.create",
        "incident.assign",
        "incident.resolve",
        "problem.view",
        "workflow.view",
        "workflow.execute",
    ],
    "PROBLEM_MANAGER": [
        "problem.view",
        "problem.manage",
        "incident.view",
        "change.manage",
        "change.approve",
        "metrics.view",
        "service.view",
    ],
    "ANALYST": [
        "request.view",
        "incident.view",
        "problem.view",
        "metrics.view",
        "metrics.export",
        "service.view",
    ],
    "SUPPLIES_MANAGER": [
        "resource.view",
        "resource.manage",
        "resource.create",
        "resource.delete",
        "workflow.execute",
        "change.view",
        "change.manage",
    ],
}


class Command(BaseCommand):
    help = "Seed default RBAC permissions and roles (idempotent)."

    def handle(self, *args, **options):
        perms = {}
        for code, label, module in PERMISSIONS:
            p, _ = Permission.objects.update_or_create(
                code=code, defaults={"label": label, "module": module}
            )
            perms[code] = p

        for code, perm_codes in ROLES.items():
            role, _ = Role.objects.update_or_create(
                code=code,
                defaults={"name": code.replace("_", " ").title(), "is_system": True},
            )
            role.rolepermissions.all().delete()
            for pcode in perm_codes:
                RolePermission.objects.create(role=role, permission=perms[pcode])

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {Permission.objects.count()} permissions and {Role.objects.count()} roles."
            )
        )
