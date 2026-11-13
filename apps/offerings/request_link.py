"""Associate requests with offering executions and synchronize their state."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.offerings.models import ExecutionStatus, OfferingExecution, TaskStatus
from apps.requests.models import Request, RequestStatus


def request_status(execution):
    states = {
        ExecutionStatus.PENDING: RequestStatus.NEW,
        ExecutionStatus.RUNNING: RequestStatus.IN_PROGRESS,
        ExecutionStatus.WAITING: RequestStatus.WAITING,
        ExecutionStatus.COMPLETED: RequestStatus.RESOLVED,
        ExecutionStatus.FAILED: RequestStatus.FAILED,
        ExecutionStatus.CANCELLED: RequestStatus.CANCELLED,
    }
    if execution.status == ExecutionStatus.WAITING and execution.tasks.filter(
        step_id=execution.current_step_id, status=TaskStatus.BLOCKED
    ).exists():
        return RequestStatus.FAILED
    try:
        return states[execution.status]
    except KeyError as exc:
        raise ValidationError(_("Unknown execution status.")) from exc


def sync_request_status(execution):
    if not execution.subject_type_id or not execution.subject_id:
        return
    request_type = ContentType.objects.get_for_model(Request)
    if execution.subject_type_id != request_type.pk:
        return
    status = request_status(execution)
    Request.objects.filter(pk=execution.subject_id).exclude(status=status).update(status=status)


@transaction.atomic
def attach_missing_requests():
    """Create requests for unassociated executions without changing other subjects."""
    for execution in (
        OfferingExecution.objects.select_for_update(of=("self",))
        .filter(subject_type__isnull=True, subject_id__isnull=True)
        .select_related("offering", "created_by")
        .order_by("pk")
    ):
        request = Request.objects.create(
            service_id=execution.offering.service_id,
            offering=execution.offering,
            title=f"{execution.offering.name} — #{execution.pk}",
            requester=execution.created_by,
            created_by=execution.created_by,
            priority=execution.offering.default_priority,
            status=request_status(execution),
        )
        Request.objects.filter(pk=request.pk).update(
            created_at=execution.started_at or execution.created_at
        )
        execution.subject = request
        execution.save(update_fields=["subject_type", "subject_id", "updated_at"])
