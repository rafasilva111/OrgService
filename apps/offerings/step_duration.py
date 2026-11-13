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


def average_durations(durations):
    if not durations:
        return None, None, None
    active = sum((active for active, _ in durations), timedelta()) / len(durations)
    elapsed = sum((elapsed for _, elapsed in durations), timedelta()) / len(durations)
    paused = sum((elapsed - active for active, elapsed in durations), timedelta()) / len(durations)
    return active, elapsed, paused


def pause_prefetch():
    return Prefetch(
        "activities",
        queryset=TaskActivity.objects.filter(action__in=("paused", "resumed")).order_by(
            "created_at", "pk"
        ),
        to_attr="pause_events",
    )


def reliable_sample(task):
    if task.started_at is None or task.completed_at is None:
        return None
    duration = active_duration(task)
    if duration is None:
        logger.warning("Excluding task %s from step average: invalid pause history", task.pk)
        return None
    return duration, task.completed_at - task.started_at


def add_person_step_statistics(people, step):
    """Count all completed visits and average reliable timings by final assignee."""
    people = list(people)
    by_id = {person.pk: person for person in people}
    samples = defaultdict(list)
    for person in people:
        person.completed_task_count = 0
    tasks = OfferingTask.objects.filter(
        execution__offering=step.offering,
        step=step,
        assigned_to_id__in=by_id,
        status=TaskStatus.DONE,
    ).prefetch_related(pause_prefetch())
    for task in tasks:
        by_id[task.assigned_to_id].completed_task_count += 1
        sample = reliable_sample(task)
        if sample is not None:
            samples[task.assigned_to_id].append(sample)
    for person in people:
        durations = samples[person.pk]
        (
            person.average_duration,
            person.average_elapsed_duration,
            person.average_pause_duration,
        ) = average_durations(durations)
        person.average_sample_count = len(durations)
    return people


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
    ).prefetch_related(pause_prefetch())
    for task in tasks:
        sample = reliable_sample(task)
        if sample is not None:
            samples[task.step_id].append(sample)
    for step in steps:
        if step.pk in step_ids:
            durations = samples[step.pk]
            step.show_average_duration = True
            (
                step.average_duration,
                step.average_elapsed_duration,
                step.average_pause_duration,
            ) = average_durations(durations)
            step.average_sample_count = len(durations)
    return steps
