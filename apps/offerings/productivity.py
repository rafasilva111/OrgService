"""Scoped productivity statistics for versioned service offerings."""

from datetime import datetime, time, timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.offerings.models import ExecutionStatus, OfferingTask, StepType, TaskStatus


def period_start(period, now):
    today = timezone.localtime(now).date()
    start = today.replace(month=1, day=1) if period == "year" else today - timedelta(
        days=int(period) - 1
    )
    return timezone.make_aware(datetime.combine(start, time.min), timezone.get_current_timezone())


def offering_totals(executions, start, end):
    current_blocked = OfferingTask.objects.filter(
        execution_id=OuterRef("pk"),
        step_id=OuterRef("current_step_id"),
        status=TaskStatus.BLOCKED,
    )
    started = Q(started_at__gte=start, started_at__lte=end)
    completed = Q(
        status=ExecutionStatus.COMPLETED, completed_at__gte=start, completed_at__lte=end
    )
    failed = Q(status=ExecutionStatus.FAILED) | Q(
        status=ExecutionStatus.WAITING, current_blocked=True
    )
    return (
        executions.annotate(current_blocked=Exists(current_blocked))
        .values("offering__service_id", "offering__name")
        .annotate(
            started=Count("pk", filter=started),
            completed=Count("pk", filter=completed),
            running=Count("pk", filter=started & Q(status=ExecutionStatus.RUNNING)),
            waiting=Count(
                "pk", filter=started & Q(status=ExecutionStatus.WAITING) & ~failed
            ),
            failed=Count("pk", filter=started & failed),
        )
    )


def offering_detail(executions, start, end):
    tasks = (
        OfferingTask.objects.filter(
            execution__in=executions,
            status=TaskStatus.DONE,
            completed_at__gte=start,
            completed_at__lte=end,
        )
        .exclude(step__step_type=StepType.NOTIFY)
        .values("assigned_to_id", "assigned_to__full_name")
        .annotate(total=Count("pk"))
        .order_by("-total", "assigned_to__full_name", "assigned_to_id")
    )
    started = (
        executions.filter(started_at__gte=start, started_at__lte=end)
        .annotate(day=TruncDate("started_at"))
        .values("day")
        .annotate(total=Count("pk"))
    )
    completed = (
        executions.filter(
            status=ExecutionStatus.COMPLETED, completed_at__gte=start, completed_at__lte=end
        )
        .annotate(day=TruncDate("completed_at"))
        .values("day")
        .annotate(total=Count("pk"))
    )
    days = {}
    for row in started:
        days.setdefault(row["day"], {"started": 0, "completed": 0})["started"] = row["total"]
    for row in completed:
        days.setdefault(row["day"], {"started": 0, "completed": 0})["completed"] = row["total"]
    timeline = [
        {"day": day, **counts}
        for day, counts in sorted(days.items())
    ]
    return list(tasks), timeline
