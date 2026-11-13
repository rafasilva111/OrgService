"""Transactional execution and task lifecycle."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.common.mixins import user_has_scoped_permission
from apps.offerings.models import (
    ExecutionNote,
    ExecutionStatus,
    Notification,
    OfferingExecution,
    OfferingStatus,
    OfferingTask,
    PauseReason,
    StepType,
    TaskActivity,
    TaskStatus,
    TransitionOutcome,
)
from apps.people.models import Person, get_system_person

ACTIVE = (ExecutionStatus.RUNNING, ExecutionStatus.WAITING)


def _actor(user):
    person = Person.objects.filter(user=user).first()
    if person is None:
        raise ValidationError(_("Your account must be linked to a person to perform this action."))
    return person


def eligible_people(step):
    from apps.common.authorization import membership_service_scope

    service = step.effective_service
    people = Person.objects.filter(active=True, deleted_at__isnull=True, user__is_active=True)
    if step.responsible_role_id:
        if not step.responsible_role.active or step.responsible_role.deleted_at is not None:
            return []
        people = people.filter(models_role_filter(step, service)).distinct()
    result = []
    for person in people.select_related("user").order_by("full_name"):
        if not user_has_scoped_permission(person.user, "offering.execute", service=service):
            continue
        if step.responsible_role_id:
            is_actor = person.service_actor_links.filter(
                service=service, role=step.responsible_role, active=True
            ).exists()
            memberships = person.memberships.filter(role=step.responsible_role, active=True)
            if not is_actor and not any(
                service in membership_service_scope(membership) for membership in memberships
            ):
                continue
        result.append(person)
    return result


def models_role_filter(step, service):
    from django.db.models import Q

    return Q(memberships__role=step.responsible_role, memberships__active=True) | Q(
        service_actor_links__service=service,
        service_actor_links__role=step.responsible_role,
        service_actor_links__active=True,
    )


def _transition(step, outcome):
    transition = step.outgoing.select_related("to_step").filter(outcome=outcome).first()
    if transition is None or transition.to_step.offering_id != step.offering_id:
        raise ValidationError(_("This step has no valid transition for the chosen outcome."))
    return transition.to_step


def _activity(task, actor, action, detail=""):
    TaskActivity.objects.create(task=task, actor=actor, action=action, detail=detail)


def _enter(execution, step, actor):
    seen = set()
    while True:
        if step.offering_id != execution.offering_id:
            raise ValidationError(_("A transition points outside this offering."))
        execution.current_step = step
        if step.step_type == StepType.END:
            execution.status = ExecutionStatus.COMPLETED
            execution.completed_at = timezone.now()
            execution.save(update_fields=["current_step", "status", "completed_at", "updated_at"])
            return
        if step.step_type in (StepType.START, StepType.NOTIFY):
            if step.pk in seen:
                raise ValidationError(_("The offering contains an automatic step cycle."))
            seen.add(step.pk)
            if step.step_type == StepType.NOTIFY:
                subject = execution.subject
                from apps.requests.models import Request

                if not isinstance(subject, Request) or not subject.requester_id or not step.message:
                    raise ValidationError(_("The notification requires a requester and a message."))
                now = timezone.now()
                task = OfferingTask.objects.create(
                    execution=execution,
                    step=step,
                    title=step.name,
                    status=TaskStatus.DONE,
                    started_at=now,
                    completed_at=now,
                )
                Notification.objects.create(
                    recipient=subject.requester, task=task, message=step.message
                )
                _activity(task, None, "notified")
            step = _transition(step, TransitionOutcome.SUCCESS)
            continue
        if step.step_type not in (StepType.TASK, StepType.APPROVAL, StepType.GATEWAY):
            raise ValidationError(_("Unsupported execution step type."))
        now = timezone.now()
        task = OfferingTask.objects.create(
            execution=execution,
            step=step,
            title=step.name,
            deadline=now + timedelta(minutes=step.sla_minutes)
            if step.sla_minutes is not None
            else None,
        )
        _activity(task, get_system_person(), "created")
        execution.status = ExecutionStatus.RUNNING
        execution.save(update_fields=["current_step", "status", "updated_at"])
        return


@transaction.atomic
def start_request_execution(request_obj, actor):
    from django.contrib.contenttypes.models import ContentType

    from apps.requests.models import Request

    Request.objects.select_for_update().get(pk=request_obj.pk)
    if OfferingExecution.objects.filter(
        subject_type=ContentType.objects.get_for_model(Request), subject_id=request_obj.pk
    ).exists():
        raise ValidationError(_("This request already has an execution."))
    offering = request_obj.offering
    if offering.status != OfferingStatus.ACTIVE or not offering.on_request:
        raise ValidationError(_("This offering is not available for requests."))
    starts = list(offering.steps.filter(step_type=StepType.START)[:2])
    if len(starts) != 1:
        raise ValidationError(_("This offering must have exactly one start step."))
    execution = OfferingExecution.objects.create(
        offering=offering,
        subject=request_obj,
        created_by=actor,
        status=ExecutionStatus.RUNNING,
        started_at=timezone.now(),
    )
    _enter(execution, starts[0], actor)
    return execution


@transaction.atomic
def task_action(task_id, user, action, *, assignee_id=None, outcome=None, reason=None, comment=None):
    task = OfferingTask.objects.select_related(
        "execution", "step__offering__service", "step__service_offering__service"
    ).get(pk=task_id)
    execution = OfferingExecution.objects.select_for_update().get(pk=task.execution_id)
    task = (
        OfferingTask.objects.select_for_update(of=("self",))
        .select_related("step__offering__service", "step__service_offering__service")
        .get(pk=task_id)
    )
    service = task.step.effective_service
    can_manage = user_has_scoped_permission(user, "offering.manage", service=service)
    can_execute = user_has_scoped_permission(user, "offering.execute", service=service)
    if not can_manage and not can_execute:
        raise PermissionDenied
    actor = _actor(user)
    if action in ("pause", "resume") and (
        action == "pause" or task.status == TaskStatus.PAUSED
    ):
        if not (can_manage or (can_execute and task.assigned_to_id == actor.pk)):
            raise PermissionDenied
    elif not (can_manage if action in ("assign", "retry", "branch", "resume") else can_execute):
        raise PermissionDenied
    if execution.status not in ACTIVE or execution.current_step_id != task.step_id:
        raise ValidationError(_("This task is no longer the current step."))
    latest = (
        execution.tasks.filter(
            step_id=task.step_id,
            status__in=(
                TaskStatus.PENDING,
                TaskStatus.ASSIGNED,
                TaskStatus.IN_PROGRESS,
                TaskStatus.PAUSED,
                TaskStatus.BLOCKED,
            ),
        )
        .order_by("-pk")
        .first()
    )
    if latest is None or latest.pk != task.pk:
        raise ValidationError(_("This task is no longer the current step."))
    if execution.status == ExecutionStatus.WAITING and action not in ("retry", "branch", "resume"):
        raise ValidationError(_("This execution is waiting for a manager decision."))
    if action == "pause":
        if execution.status != ExecutionStatus.RUNNING or task.status != TaskStatus.IN_PROGRESS:
            raise ValidationError(_("This task cannot be paused."))
        if reason not in PauseReason.values:
            raise ValidationError(_("Choose a valid pause reason."))
        comment = (comment or "").strip()
        if reason == PauseReason.COMMENT and not comment:
            raise ValidationError(_("A comment is required for this pause reason."))
        if len(comment) > 4000:
            raise ValidationError(_("The comment must not exceed 4000 characters."))
        task.status = TaskStatus.PAUSED
        task.save(update_fields=["status", "updated_at"])
        _activity(task, actor, "paused", reason)
        if comment:
            ExecutionNote.objects.create(
                execution=execution, task=task, author=user, body=comment,
                kind=ExecutionNote.Kind.COMMENT,
            )
        execution.status = ExecutionStatus.WAITING
        execution.save(update_fields=["status", "updated_at"])
    elif action == "resume":
        if execution.status != ExecutionStatus.WAITING or task.status not in (
            TaskStatus.PENDING, TaskStatus.PAUSED
        ):
            raise ValidationError(_("This task cannot be resumed."))
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.IN_PROGRESS
            task.save(update_fields=["status", "updated_at"])
        execution.status = ExecutionStatus.RUNNING
        execution.save(update_fields=["status", "updated_at"])
        _activity(task, actor, "resumed")
    elif action == "assign":
        if task.status not in (TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
            raise ValidationError(_("This task cannot be assigned now."))
        person = next(
            (p for p in eligible_people(task.step) if str(p.pk) == str(assignee_id)), None
        )
        if person is None:
            raise ValidationError(_("Choose an eligible person."))
        if task.assigned_to_id == person.pk:
            raise ValidationError(_("This task is already assigned to that person."))
        task.assigned_to = person
        if task.status != TaskStatus.IN_PROGRESS:
            task.status = TaskStatus.ASSIGNED
        task.save(update_fields=["assigned_to", "status", "updated_at"])
        _activity(task, actor, "assigned", person.full_name)
    elif action == "claim":
        if task.step.responsible_role_id is None:
            raise ValidationError(_("This task is not assigned to a group."))
        if task.status != TaskStatus.PENDING or task.assigned_to_id is not None:
            raise ValidationError(_("This task has already been claimed or assigned."))
        if actor.pk not in {person.pk for person in eligible_people(task.step)}:
            raise PermissionDenied
        task.assigned_to = actor
        task.status = TaskStatus.ASSIGNED
        task.save(update_fields=["assigned_to", "status", "updated_at"])
        _activity(task, actor, "claimed")
    elif action == "start":
        if task.status not in (TaskStatus.PENDING, TaskStatus.ASSIGNED) or (
            task.assigned_to_id and task.assigned_to_id != actor.pk
        ) or (task.step.responsible_role_id and not task.assigned_to_id):
            raise ValidationError(_("This task cannot be started by you."))
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at", "updated_at"])
        _activity(task, actor, "started")
    elif action in ("complete", "fail"):
        if task.status != TaskStatus.IN_PROGRESS or (
            task.assigned_to_id and task.assigned_to_id != actor.pk
        ):
            raise ValidationError(_("This task cannot be completed by you."))
        if action == "fail":
            task.status = TaskStatus.BLOCKED
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            execution.status = ExecutionStatus.WAITING
            execution.save(update_fields=["status", "updated_at"])
            _activity(task, actor, "failed")
        else:
            chosen = (
                outcome if task.step.step_type == StepType.GATEWAY else TransitionOutcome.SUCCESS
            )
            if chosen not in TransitionOutcome.values:
                raise ValidationError(_("Choose a valid transition."))
            target = _transition(task.step, chosen)
            task.status = TaskStatus.DONE
            if task.completed_at is None:
                task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            _activity(task, actor, "completed", chosen)
            _enter(execution, target, actor)
    elif action in ("retry", "branch"):
        if task.status != TaskStatus.BLOCKED or execution.status != ExecutionStatus.WAITING:
            raise ValidationError(_("This task is not awaiting a decision."))
        target = (
            task.step if action == "retry" else _transition(task.step, TransitionOutcome.FAILURE)
        )
        task.status = TaskStatus.CANCELLED
        if task.completed_at is None:
            task.completed_at = timezone.now()
        task.save(update_fields=["status", "completed_at", "updated_at"])
        _activity(task, actor, action)
        _enter(execution, target, actor)
    else:
        raise ValidationError(_("Unknown task action."))
