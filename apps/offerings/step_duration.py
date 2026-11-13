"""Historical active-time averages for manual offering steps."""

import logging
from collections import defaultdict
from datetime import timedelta

from django.db.models import Prefetch

from apps.offerings.models import OfferingTask, StepType, TaskActivity, TaskStatus

logger = logging.getLogger(__name__)

MANUAL_STEPS = (StepType.TASK, StepType.APPROVAL, StepType.GATEWAY)


def active_duration(task):
    """Return None if timestamps or pause history cannot establish active time."""
    if task.completed_at < task.started_at:
        return None
    paused_at = None
    paused = timedelta()
    for event in task.pause_events:
        if not task.started_at <= event.created_at <= task.completed_at:
            return None
        if event.action == "paused":
            if paused_at is not None:
                return None
            paused_at = event.created_at
        else:
            if paused_at is None:
                return None
            paused += event.created_at - paused_at
            paused_at = None
    if paused_at is not None:
        return None
    return task.completed_at - task.started_at - paused


def add_step_averages(steps, offering):
    """Populate each manual step with a same-version historical task average."""
    steps = list(steps)
    step_ids = [step.pk for step in steps if step.step_type in MANUAL_STEPS]
    samples = defaultdict(list)
    tasks = OfferingTask.objects.filter(
        execution__offering=offering,
        step_id__in=step_ids,
        status=TaskStatus.DONE,
        started_at__isnull=False,
        completed_at__isnull=False,
    ).prefetch_related(
        Prefetch(
            "activities",
            queryset=TaskActivity.objects.filter(action__in=("paused", "resumed")).order_by(
                "created_at", "pk"
            ),
            to_attr="pause_events",
        )
    )
    for task in tasks:
        duration = active_duration(task)
        if duration is None:
            logger.warning("Excluding task %s from step average: invalid pause history", task.pk)
            continue
        samples[task.step_id].append(duration)
    for step in steps:
        if step.pk in step_ids:
            durations = samples[step.pk]
            step.show_average_duration = True
            step.average_duration = sum(durations, timedelta()) / len(durations) if durations else None
    return steps
