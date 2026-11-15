"""Derive an execution deadline from the normal offering path."""

from datetime import timedelta

from apps.offerings.builder import steps_queryset
from apps.offerings.models import StepType
from apps.offerings.step_duration import MANUAL_STEPS


def complete_path_sla_minutes(offering):
    steps = list(steps_queryset(offering))
    starts = [step for step in steps if step.step_type == StepType.START]
    if len(starts) != 1:
        return None
    by_id = {step.pk: step for step in steps}
    step = starts[0]
    seen = set()
    minutes = 0
    while step and step.pk not in seen:
        if step.step_type == StepType.END:
            return minutes
        seen.add(step.pk)
        if step.step_type in MANUAL_STEPS and step.sla_minutes is None:
            return None
        minutes += step.sla_minutes or 0
        target = step.next_step
        step = by_id.get(target.pk) if target is not None else None
    return None


def execution_final_deadline(execution):
    if execution.started_at is None:
        return None
    minutes = complete_path_sla_minutes(execution.offering)
    if minutes is None:
        return None
    return execution.started_at + timedelta(minutes=minutes)
