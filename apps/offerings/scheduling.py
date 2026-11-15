"""Scheduled offering execution and manager notifications."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.offerings.models import (
    ExecutionStatus,
    Notification,
    OfferingExecution,
    OfferingStatus,
    ScheduledOccurrence,
    ServiceOffering,
    StepType,
)
from apps.people.models import get_system_person
from apps.requests.models import Request
from apps.services.models import SERVICE_MANAGER_ROLE, Service, ServiceActor


def notify_managers(execution, task, message):
    managers = (
        ServiceActor.objects.filter(
            service=execution.offering.service,
            active=True,
            role__code=SERVICE_MANAGER_ROLE,
            role__active=True,
            role__deleted_at__isnull=True,
            person__active=True,
            person__deleted_at__isnull=True,
        )
        .values_list("person_id", flat=True)
        .distinct()
    )
    recipients = list(managers)
    if not recipients:
        raise ValidationError(_("The service has no active service manager to notify."))
    Notification.objects.bulk_create(
        [
            Notification(recipient_id=person_id, task=task, message=message)
            for person_id in recipients
        ]
    )


@transaction.atomic
def process_offering(offering_id, today):
    from apps.offerings.execution import _enter

    offering = ServiceOffering.objects.get(pk=offering_id)
    Service.objects.select_for_update().get(pk=offering.service_id)
    offering.refresh_from_db()
    if offering.status != OfferingStatus.ACTIVE or not offering.is_recurring:
        return None
    logical = ScheduledOccurrence.objects.filter(
        offering__service_id=offering.service_id, offering__name=offering.name
    )
    pending = logical.filter(execution__isnull=True).order_by("scheduled_date", "pk").first()
    if offering.next_occurrences(count=1, after=today) == [today]:
        if pending and pending.covered_through < today:
            pending.covered_through = today
            pending.save(update_fields=["covered_through", "updated_at"])
        elif pending is None and not logical.filter(covered_through=today).exists():
            pending = ScheduledOccurrence.objects.create(
                offering=offering, scheduled_date=today, covered_through=today
            )
    if pending is None:
        return None
    if logical.filter(
        execution__status__in=(
            ExecutionStatus.PENDING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.WAITING,
            ExecutionStatus.FAILED,
        )
    ).exists():
        return None
    starts = list(offering.steps.filter(step_type=StepType.START)[:2])
    if len(starts) != 1:
        raise ValidationError(_("This offering must have exactly one start step."))
    system = get_system_person()
    request = Request.objects.create(
        service=offering.service,
        offering=offering,
        title=offering.name,
        requester=system,
        created_by=system,
        priority=offering.default_priority,
    )
    execution = OfferingExecution.objects.create(
        offering=offering,
        subject_type=ContentType.objects.get_for_model(Request),
        subject_id=request.pk,
        created_by=system,
        status=ExecutionStatus.RUNNING,
        started_at=timezone.now(),
    )
    pending.execution = execution
    pending.save(update_fields=["execution", "updated_at"])
    _enter(execution, starts[0], system)
    task = execution.tasks.order_by("pk").first()
    if task is None:
        raise ValidationError(_("A recurring execution must create at least one task."))
    notify_managers(
        execution, task, _("Scheduled execution started: %(name)s") % {"name": offering.name}
    )
    return execution
