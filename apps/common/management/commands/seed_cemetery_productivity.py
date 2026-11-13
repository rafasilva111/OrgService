"""Create a repeatable, explicitly requested cemetery productivity dataset."""

import calendar
from datetime import datetime, time, timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.common.management.commands.seed_default_data import Command as DefaultSeed
from apps.offerings.execution import eligible_people
from apps.offerings.models import (
    ExecutionStatus,
    Notification,
    OfferingExecution,
    OfferingTask,
    PauseReason,
    ServiceOffering,
    StepType,
    TaskActivity,
    TaskStatus,
    TransitionOutcome,
)
from apps.people.models import Person, get_system_person
from apps.requests.models import Request, RequestAttachment
from apps.services.models import Service

MARKER = "[demo-cemetery-productivity-v1:"
OFFERINGS = ("Limpar cemitério", "Realizar um funeral", "Realizar transladação")


def three_months_before(day):
    month = day.month - 3
    year = day.year
    if month <= 0:
        month += 12
        year -= 1
    return day.replace(
        year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])
    )


def main_path(offering):
    starts = list(offering.steps.filter(step_type=StepType.START))
    if len(starts) != 1:
        raise CommandError(f"{offering}: expected exactly one start step.")
    path, seen, current = [], set(), starts[0]
    while True:
        if current.pk in seen or current.offering_id != offering.pk:
            raise CommandError(f"{offering}: invalid or cyclic success path.")
        seen.add(current.pk)
        path.append(current)
        if current.step_type == StepType.END:
            return path
        transitions = list(current.outgoing.filter(outcome=TransitionOutcome.SUCCESS))
        if len(transitions) != 1:
            raise CommandError(f"{offering}: expected exactly one success transition.")
        current = transitions[0].to_step


def record_activity(task, actor, action, when, detail=""):
    activity = TaskActivity.objects.create(task=task, actor=actor, action=action, detail=detail)
    TaskActivity.objects.filter(pk=activity.pk).update(created_at=when, updated_at=when)


def sample_answers(name, started, index):
    if name == OFFERINGS[0]:
        return {"zonas": ["Arruamentos", "Zonas verdes"], "email": "demo@example.invalid"}, None
    if name == OFFERINGS[1]:
        return {
            "nome-do-falecido": f"Pessoa fictícia {index:03d}",
            "data-do-obito": (started.date() - timedelta(days=2)).isoformat(),
            "tipo-de-sepultura": "Sepultura temporária",
            "velorio": False,
            "contacto": "910000000",
            "observacoes": "Dados fictícios para demonstração.",
        }, "certidao-de-obito"
    return {
        "nome-do-falecido": f"Pessoa fictícia {index:03d}",
        "cemiterio-de-destino": {
            "street": "Rua Exemplo, 1",
            "postal_code": "2560-100",
            "locality": "Torres Vedras",
        },
        "contacto": "910000000",
    }, "autorizacao"


class Command(BaseCommand):
    help = "Add 100 fictitious cemetery requests and executions for productivity demos."

    def handle(self, *args, **options):
        service = Service.objects.filter(
            name="Cemitérios", organization__code="santa-maria-sao-pedro-matacaes"
        ).first()
        if service is None:
            raise CommandError("Seed the default cemetery service before running this command.")
        requester = Person.objects.filter(user__username="p.lopes", active=True).first()
        if requester is None:
            raise CommandError("The active demo requester p.lopes is required.")
        offerings = {}
        for name in OFFERINGS:
            offering = ServiceOffering.objects.filter(service=service, name=name, version=1).first()
            if offering is None:
                raise CommandError(f"Missing demo offering: {name} v1.")
            path = main_path(offering)
            candidates = {}
            for step in path:
                if step.step_type in (StepType.TASK, StepType.APPROVAL, StepType.GATEWAY):
                    candidates[step.pk] = eligible_people(step)
                    if not candidates[step.pk]:
                        raise CommandError(f"No eligible assignees for {offering} / {step.name}.")
            offerings[name] = (offering, path, candidates)

        now = timezone.now()
        today = timezone.localdate(now)
        first = three_months_before(today)
        span = (today - first).days
        system = get_system_person()
        request_type = ContentType.objects.get_for_model(Request)
        saved_files = []
        try:
            with transaction.atomic():
                Service.objects.select_for_update().get(pk=service.pk)
                existing = Request.objects.filter(service=service, description__startswith=MARKER)
                if existing.exists():
                    markers = set(existing.values_list("description", flat=True))
                    expected = {f"{MARKER}{i:03d}] Dados fictícios." for i in range(1, 101)}
                    if existing.count() == 100 and markers == expected:
                        self.stdout.write(
                            "The 100 marked demo requests already exist; nothing changed."
                        )
                        return
                    raise CommandError(
                        f"Found {existing.count()} marked requests with an incomplete or altered "
                        "cohort; inspect first."
                    )
                for index in range(100):
                    name = OFFERINGS[index % len(OFFERINGS)]
                    offering, path, candidates = offerings[name]
                    day = first + timedelta(days=span * index // 99)
                    started = timezone.make_aware(
                        datetime.combine(day, time(hour=9, minute=(index % 4) * 10)),
                        timezone.get_current_timezone(),
                    )
                    started = min(started, now - timedelta(hours=6))
                    manual = [step for step in path if step.pk in candidates]
                    kind = index % 10
                    is_completed = kind < 6
                    is_blocked = kind == 6
                    is_paused = kind == 7
                    current_index = min((index // 3) % len(manual), len(manual) - 1)
                    current_step = path[-1] if is_completed else manual[current_index]
                    finish = started + timedelta(minutes=45 * len(manual))
                    request = Request.objects.create(
                        service=service,
                        offering=offering,
                        title=f"{name} — demonstração {index + 1:03d}",
                        description=f"{MARKER}{index + 1:03d}] Dados fictícios.",
                        requester=requester,
                        created_by=requester,
                        priority=offering.default_priority,
                    )
                    answers, file_key = sample_answers(name, started, index + 1)
                    if file_key:
                        content = DefaultSeed._example_pdf()
                        attachment = RequestAttachment(
                            request=request,
                            field_key=file_key,
                            original_name="exemplo-demonstracao.pdf",
                            size=len(content),
                            content_type="application/pdf",
                        )
                        attachment.file.save(
                            attachment.original_name, ContentFile(content), save=False
                        )
                        saved_files.append((attachment.file.storage, attachment.file.name))
                        attachment.save()
                        answers[file_key] = {
                            "attachment": attachment.pk,
                            "name": attachment.original_name,
                        }
                    request.form_data = answers
                    request.save(update_fields=["form_data"])
                    Request.objects.filter(pk=request.pk).update(
                        created_at=started, updated_at=started
                    )
                    execution = OfferingExecution.objects.create(
                        offering=offering,
                        subject_type=request_type,
                        subject_id=request.pk,
                        created_by=requester,
                        started_at=started,
                        current_step=current_step,
                        completed_at=finish if is_completed else None,
                        status=(
                            ExecutionStatus.COMPLETED if is_completed else ExecutionStatus.RUNNING
                        ),
                    )
                    OfferingExecution.objects.filter(pk=execution.pk).update(created_at=started)
                    for step_index, step in enumerate(manual):
                        if not is_completed and step_index > current_index:
                            break
                        begin = started + timedelta(minutes=45 * step_index)
                        done = is_completed or step_index < current_index
                        end = begin + timedelta(minutes=45)
                        assignees = candidates[step.pk]
                        assignee = assignees[(index + step_index) % len(assignees)]
                        task_status = (
                            TaskStatus.DONE
                            if done
                            else TaskStatus.BLOCKED
                            if is_blocked
                            else TaskStatus.PAUSED
                            if is_paused
                            else TaskStatus.IN_PROGRESS
                        )
                        task = OfferingTask.objects.create(
                            execution=execution,
                            step=step,
                            title=step.name,
                            assigned_to=assignee,
                            status=task_status,
                            started_at=begin,
                            completed_at=end if done or is_blocked else None,
                            deadline=begin + timedelta(minutes=step.sla_minutes)
                            if step.sla_minutes is not None
                            else None,
                        )
                        OfferingTask.objects.filter(pk=task.pk).update(created_at=begin)
                        record_activity(task, system, "created", begin)
                        record_activity(task, assignee, "assigned", begin, assignee.full_name)
                        record_activity(task, assignee, "started", begin)
                        if done:
                            record_activity(
                                task, assignee, "completed", end, TransitionOutcome.SUCCESS
                            )
                        elif is_blocked:
                            record_activity(task, assignee, "failed", end)
                        elif is_paused:
                            record_activity(task, assignee, "paused", end, PauseReason.APPROVAL)
                    if not is_completed and (is_blocked or is_paused):
                        execution.status = ExecutionStatus.WAITING
                        execution.save(update_fields=["status"])
                    if is_completed:
                        for step in path:
                            if step.step_type == StepType.NOTIFY:
                                task = OfferingTask.objects.create(
                                    execution=execution,
                                    step=step,
                                    title=step.name,
                                    status=TaskStatus.DONE,
                                    started_at=finish,
                                    completed_at=finish,
                                )
                                OfferingTask.objects.filter(pk=task.pk).update(created_at=finish)
                                Notification.objects.create(
                                    recipient=requester,
                                    task=task,
                                    message=step.message,
                                    read_at=finish,
                                )
                                record_activity(task, None, "notified", finish)
        except Exception:
            for storage, filename in saved_files:
                storage.delete(filename)
            raise
        self.stdout.write(self.style.SUCCESS("Created 100 cemetery demo requests and executions."))
