from django.core.management.base import BaseCommand

from apps.access.management.commands.seed_rbac import ROLES
from apps.access.models import Role
from apps.offerings.models import ExecutionNote
from apps.organizations.models import OrganizationMembership


class Command(BaseCommand):
    help = "Report existing role grants and memberships affected by the permission policy."

    def handle(self, *args, **options):
        for code, desired in ROLES.items():
            role = Role.objects.filter(code=code).first()
            if role is None:
                self.stdout.write(f"{code}: role not present")
                continue
            current = set(
                role.rolepermissions.filter(active=True).values_list("permission__code", flat=True)
            )
            target = set(desired)
            memberships = OrganizationMembership.objects.filter(role=role, active=True).count()
            self.stdout.write(
                f"{code}: {memberships} active memberships; "
                f"add {', '.join(sorted(target - current)) or '-'}; "
                f"outside baseline {', '.join(sorted(current - target)) or '-'}"
            )
        for role in Role.objects.filter(code__startswith="SERVICE_MANAGER_").order_by("code"):
            memberships = OrganizationMembership.objects.filter(role=role, active=True).count()
            self.stdout.write(
                f"{role.code}: {memberships} active memberships; scoped clone retained"
            )
        self.stdout.write(
            "Existing execution notes may become visible to their requesters: "
            f"{ExecutionNote.objects.count()} notes require review."
        )
