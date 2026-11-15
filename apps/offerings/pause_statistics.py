"""Pause counts and durations for a single offering step."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.offerings.models import OfferingTask, PauseReason, TaskActivity, TaskStatus
from apps.offerings.step_duration import pause_prefetch

logger = logging.getLogger(__name__)


@dataclass
class PauseSummary:
    key: str
    reason: str
    count: int = 0
    ongoing_count: int = 0
    total_duration: timedelta | None = timedelta()


@dataclass
class PauseVisit:
    task: OfferingTask
    event: TaskActivity
    key: str
    reason: str
    ongoing: bool
    duration: timedelta | None


def step_pause_visits(step, *, now=None):
    now = now or timezone.now()
    labels = dict(PauseReason.choices)
    visits = []
    tasks = (
        OfferingTask.objects.filter(
            execution__offering=step.offering, step=step, activities__action="paused"
        )
        .distinct()
        .select_related("execution", "assigned_to")
        .prefetch_related(pause_prefetch())
    )
    for task in tasks:
        events = task.pause_events
        for index, event in enumerate(events):
            if event.action != "paused":
                continue
            key = event.detail if event.detail in labels else "OTHER"
            following = events[index + 1] if index + 1 < len(events) else None
            ongoing = following is None and task.status == TaskStatus.PAUSED
            if following and following.action == "resumed":
                end = following.created_at
            elif ongoing:
                end = now
            else:
                end = None
            if (
                end is None
                or task.started_at is None
                or event.created_at < task.started_at
                or end < event.created_at
                or end > now
                or (task.completed_at is not None and end > task.completed_at)
            ):
                logger.warning(
                    "Cannot calculate duration for pause %s on task %s", event.pk, task.pk
                )
                duration = None
            else:
                duration = end - event.created_at
            visits.append(
                PauseVisit(
                    task,
                    event,
                    key,
                    labels[key] if key != "OTHER" else _("Other or unspecified"),
                    ongoing,
                    duration,
                )
            )
    return visits


def step_pause_summaries(step, *, now=None):
    summaries = {}
    for visit in step_pause_visits(step, now=now):
        if visit.key not in summaries:
            summaries[visit.key] = PauseSummary(visit.key, visit.reason)
        summary = summaries[visit.key]
        summary.count += 1
        summary.ongoing_count += int(visit.ongoing)
        if visit.duration is None:
            summary.total_duration = None
        elif summary.total_duration is not None:
            summary.total_duration += visit.duration
    return sorted(summaries.values(), key=lambda summary: (-summary.count, str(summary.reason)))
