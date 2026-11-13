from django.core.management.base import BaseCommand
from django.db import transaction

from apps.offerings.models import (
    ExecutionStatus,
    OfferingExecution,
    OfferingTask,
    StepType,
    TaskStatus,
)


class Command(BaseCommand):
    help = "Create missing current-step tasks for active executions without inventing history."

    def add_arguments(self, parser):
        parser.add_argument("--service-id", type=int, help="Limit backfill to one service.")
        parser.add_argument("--dry-run", action="store_true", help="Report without creating tasks.")

    def handle(self, *args, **options):
        created = skipped = 0
        executions = OfferingExecution.objects.filter(
            status__in=[ExecutionStatus.RUNNING, ExecutionStatus.WAITING]
        )
        if options["service_id"] is not None:
            executions = executions.filter(offering__service_id=options["service_id"])
        ids = executions.values_list("pk", flat=True)
        for pk in ids.iterator():
            with transaction.atomic():
                execution = (
                    OfferingExecution.objects.select_for_update(of=("self",))
                    .select_related("current_step")
                    .get(pk=pk)
                )
                if execution.status not in (ExecutionStatus.RUNNING, ExecutionStatus.WAITING):
                    continue
                step = execution.current_step
                if step is None or step.offering_id != execution.offering_id:
                    self.stderr.write(f"Execution #{pk}: missing or invalid current step.")
                    skipped += 1
                    continue
                if step.step_type not in (StepType.TASK, StepType.APPROVAL, StepType.GATEWAY):
                    self.stderr.write(f"Execution #{pk}: current step is not a manual task.")
                    skipped += 1
                    continue
                if not execution.tasks.filter(
                    step=step,
                    status__in=(
                        TaskStatus.PENDING,
                        TaskStatus.ASSIGNED,
                        TaskStatus.IN_PROGRESS,
                        TaskStatus.BLOCKED,
                    ),
                ).exists():
                    if not options["dry_run"]:
                        OfferingTask.objects.create(
                            execution=execution,
                            step=step,
                            title=step.name,
                        )
                    created += 1
        label = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(f"{label} {created} tasks; skipped {skipped} anomalous executions.")
