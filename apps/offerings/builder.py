"""Service offering builder: the editable work plan (steps + branches).

The editor posts the plan as JSON (``definition``): an ordered list of the
offering's working steps. ``Início`` (START) and ``Fim`` (END) are implicit and
added on save. Each step names its normal ``next`` step (default: the following
one in the list, or the end) and, optionally, an ``on_error`` step — the
FAILURE branch, e.g. a notification to the requester.

Everything here is declarative: there is no execution engine yet, so nothing
advances executions or sends messages (see ADR-015).
"""

import json
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.common.durations import duration_input, parse_duration
from apps.offerings.models import (
    OfferingStep,
    OfferingTransition,
    StepType,
    TransitionOutcome,
)

END = "end"
START = "start"


@dataclass
class StepSpec:
    """One working step of a plan, as validated from the editor."""

    key: str
    name: str
    step_type: str = StepType.TASK
    role: object = None  # access.Role | None
    sla_minutes: int | None = None
    inputs: list = field(default_factory=list)
    service_offering: object = None  # ServiceOffering | None
    message: str = ""
    next: str = ""  # step key, END, or "" = the following step
    on_error: str = ""  # step key, END, or "" = no error branch


@dataclass
class ParseResult:
    steps: list
    errors: list  # plan-wide messages
    step_errors: dict  # index -> [messages]

    @property
    def ok(self):
        return not self.errors and not self.step_errors


def _unique_key(raw, index, taken):
    key = slugify(str(raw or ""))[:48] or f"step-{index + 1}"
    if key in (START, END):
        key = f"{key}-step"
    base, n = key, 2
    while key in taken:
        key = f"{base}-{n}"
        n += 1
    taken.add(key)
    return key


def infer_step_type(*, external, message, on_error):
    """The step type is not typed in the editor; it follows from the step:
    handed to another service → task (shown with that offering's type);
    a message for the requester → notification; an error branch → approval
    (a check that can fail); otherwise a task."""
    if external:
        return StepType.TASK
    if message:
        return StepType.NOTIFY
    if on_error:
        return StepType.APPROVAL
    return StepType.TASK


def parse_definition(raw, *, roles, offerings, own_service_id=None, service_roles=None):
    """Validate the editor's JSON.

    ``roles`` / ``offerings`` are the querysets the user may pick from (the
    responsible offering must be an ACTIVE offering the user can see). Returns
    a ``ParseResult``; invalid values are dropped from the specs so a preview
    can still render while errors are reported per step.

    A delegated step uses the responsible offering's SLA and service roles.
    """
    errors, step_errors = [], {}
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ParseResult([], [_("The work plan could not be read.")], {})
    if not isinstance(data, list):
        return ParseResult([], [_("The work plan could not be read.")], {})

    role_map = {r.pk: r for r in roles}
    offering_map = {o.pk: o for o in offerings}
    taken, specs, raw_keys = set(), [], {}

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            item = {}
        problems = []
        key = _unique_key(item.get("key"), index, taken)
        raw_keys[str(item.get("key") or "")] = key
        name = str(item.get("name") or "").strip()[:255]
        if not name:
            problems.append(_("Give the step a name."))
        service_offering = None
        if item.get("service_offering") not in (None, ""):
            try:
                service_offering = offering_map.get(int(item["service_offering"]))
            except (TypeError, ValueError):
                service_offering = None
            if service_offering is None:
                problems.append(_("The responsible service offering is not available."))
        external = service_offering is not None

        role, sla = None, None
        if item.get("role") not in (None, ""):
            try:
                role = role_map.get(int(item["role"]))
            except (TypeError, ValueError):
                role = None
            service_id = service_offering.service_id if external else own_service_id
            if role is None or (
                service_roles is not None
                and role.pk not in {r.pk for r in service_roles.get(service_id, [])}
            ):
                problems.append(_("The role is not available for the responsible service."))
                role = None
        if external:
            sla = service_offering.sla_minutes
        else:
            try:
                sla = parse_duration(item.get("sla"))
            except (TypeError, ValueError):
                problems.append(_("SLA must be a duration such as 1d 4h 30m."))
                sla = None

        inputs = item.get("inputs") or []
        if isinstance(inputs, str):
            inputs = inputs.split(",")
        inputs = [str(i).strip() for i in inputs if str(i).strip()]

        message = "" if external else str(item.get("message") or "").strip()
        step_type = infer_step_type(
            external=external, message=message, on_error=str(item.get("on_error") or "")
        )

        specs.append(
            StepSpec(
                key=key,
                name=name,
                step_type=step_type,
                role=role,
                sla_minutes=sla,
                inputs=inputs,
                service_offering=service_offering,
                message=message,
                next=str(item.get("next") or ""),
                on_error=str(item.get("on_error") or ""),
            )
        )
        if problems:
            step_errors[index] = problems

    # Resolve references (client keys -> normalized keys) and defaults.
    keys = {s.key for s in specs}
    for index, spec in enumerate(specs):
        for attr in ("next", "on_error"):
            ref = getattr(spec, attr)
            if not ref:
                continue
            target = END if ref == END else raw_keys.get(ref, ref)
            if target != END and target not in keys:
                step_errors.setdefault(index, []).append(_("Points to a step that does not exist."))
                target = ""
            elif target == spec.key:
                step_errors.setdefault(index, []).append(_("A step cannot point to itself."))
                target = ""
            setattr(spec, attr, target)
        if not spec.next:
            spec.next = specs[index + 1].key if index + 1 < len(specs) else END
        if spec.step_type == StepType.APPROVAL and not spec.on_error:
            spec.step_type = StepType.TASK  # its error branch was invalid and dropped
    return ParseResult(specs, errors, step_errors)


def validate_graph(specs):
    """Plan-wide rules checked on publish. Returns a list of messages."""
    if not specs:
        return [_("Add at least one step.")]
    by_key = {s.key: s for s in specs}
    edges = {START: [specs[0].key]}
    for s in specs:
        edges[s.key] = [t for t in (s.next, s.on_error) if t]

    reachable, stack = set(), [START]
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        stack.extend(edges.get(node, []))

    reverse = {}
    for src, targets in edges.items():
        for t in targets:
            reverse.setdefault(t, []).append(src)
    finishes, stack = set(), [END]
    while stack:
        node = stack.pop()
        if node in finishes:
            continue
        finishes.add(node)
        stack.extend(reverse.get(node, []))

    errors = []
    unreachable = [by_key[k].name for k in by_key if k not in reachable]
    if unreachable:
        errors.append(
            _("These steps can never be reached: %(steps)s.") % {"steps": ", ".join(unreachable)}
        )
    stuck = [by_key[k].name for k in by_key if k in reachable and k not in finishes]
    if stuck:
        errors.append(
            _("These steps never lead to the end: %(steps)s.") % {"steps": ", ".join(stuck)}
        )
    return errors


@transaction.atomic
def save_definition(offering, specs):
    """Replace a draft's steps and transitions with ``specs``."""
    if not offering.is_editable:
        raise ValidationError(_("Only draft offerings can be edited."))
    if offering.executions.exists():
        raise ValidationError(_("This offering already has executions."))
    offering.transitions.all().delete()
    offering.steps.all().delete()

    start = OfferingStep.objects.create(
        offering=offering, code=START, name=_("Start"), step_type=StepType.START, order=10
    )
    created = {}
    for index, spec in enumerate(specs):
        created[spec.key] = OfferingStep.objects.create(
            offering=offering,
            code=spec.key,
            name=spec.name or _("Step %(n)s") % {"n": index + 1},
            step_type=spec.step_type,
            order=20 + index * 10,
            responsible_role=spec.role,
            sla_minutes=spec.sla_minutes,
            required_inputs=spec.inputs,
            service_offering=spec.service_offering,
            message=spec.message,
        )
    end = OfferingStep.objects.create(
        offering=offering,
        code=END,
        name=_("End"),
        step_type=StepType.END,
        order=20 + len(specs) * 10,
    )
    created[END] = end

    def link(src, dst, outcome):
        OfferingTransition.objects.create(
            offering=offering,
            from_step=src,
            to_step=dst,
            outcome=outcome,
            order=10 if outcome == TransitionOutcome.SUCCESS else 20,
        )

    link(start, created[specs[0].key] if specs else end, TransitionOutcome.SUCCESS)
    for spec in specs:
        src = created[spec.key]
        if spec.next in created:
            link(src, created[spec.next], TransitionOutcome.SUCCESS)
        if spec.on_error in created:
            link(src, created[spec.on_error], TransitionOutcome.FAILURE)
    offering.sla_minutes = specs_sla(specs)
    offering.save(update_fields=["sla_minutes", "updated_at"])
    sync_offering_sla(offering)
    return offering


def specs_sla(specs):
    """Total SLA of an (unsaved) plan: step SLAs along the normal path."""
    by_key = {s.key: s for s in specs}
    current = specs[0].key if specs else END
    total, seen = 0, set()
    while current in by_key and current not in seen:
        seen.add(current)
        spec = by_key[current]
        total += spec.sla_minutes or 0
        current = spec.next
    return total


def offering_sla(offering):
    """Total SLA of a saved offering: step SLAs from Início to Fim following the
    normal (SUCCESS) transitions; error branches are not counted."""
    steps = list(steps_queryset(offering))
    if not steps:
        return None
    current = next((s for s in steps if s.step_type == StepType.START), steps[0])
    total, seen = 0, set()
    while current is not None and current.pk not in seen and current.step_type != StepType.END:
        seen.add(current.pk)
        total += current.sla_minutes or 0
        current = current.next_step
    return total


def refresh_offering_sla(offering):
    offering.sla_minutes = offering_sla(offering)
    offering.save(update_fields=["sla_minutes", "updated_at"])
    sync_offering_sla(offering)
    return offering.sla_minutes


def sync_offering_sla(offering):
    """Create/update the offering's resolution SLA (``metrics.SLA``) from its
    total SLA. Active only while the offering version is ACTIVE; removed when
    the offering has no total."""
    from apps.metrics.models import SLA, ScopeType
    from apps.offerings.models import OfferingStatus

    if offering.sla_minutes is None:
        SLA.objects.filter(offering=offering).delete()
        return None
    sla, _created = SLA.objects.update_or_create(
        offering=offering,
        scope=ScopeType.RESOLUTION,
        defaults={
            "service_id": offering.service_id,
            "name": f"{offering.name} v{offering.version}"[:128],
            "target_minutes": offering.sla_minutes,
            "active": offering.status == OfferingStatus.ACTIVE,
        },
    )
    return sla


def steps_queryset(offering):
    """Ordered steps with everything the steps card reads, prefetched."""
    return (
        offering.steps.select_related(
            "responsible_role", "offering__service", "service_offering__service"
        )
        .prefetch_related("outgoing__to_step")
        .order_by("order")
    )


def definition_from_offering(offering):
    """The editor JSON for an existing offering (working steps only)."""
    steps = [
        s for s in steps_queryset(offering) if s.step_type not in (StepType.START, StepType.END)
    ]
    keys = [s.code for s in steps]
    out = []
    for index, step in enumerate(steps):
        nxt, err = step.next_step, step.failure_step
        following = keys[index + 1] if index + 1 < len(keys) else END
        nxt_key = (
            END if nxt is not None and nxt.step_type == StepType.END else (nxt.code if nxt else "")
        )
        err_key = ""
        if err is not None:
            err_key = END if err.step_type == StepType.END else err.code
        out.append(
            {
                "key": step.code,
                "name": step.name,
                "step_type": step.step_type,
                "role": step.responsible_role_id,
                "sla": duration_input(step.sla_minutes),
                "inputs": ", ".join(step.required_inputs or []),
                "service_offering": step.service_offering_id,
                "message": step.message,
                # Default ("") when it simply follows the list order.
                "next": "" if nxt_key == following else nxt_key,
                "on_error": err_key,
            }
        )
    return out


def validate_offering_graph(offering):
    """Raise ``ValidationError`` if a saved offering's plan cannot be published."""
    steps = [
        s for s in steps_queryset(offering) if s.step_type not in (StepType.START, StepType.END)
    ]
    specs = []
    for step in steps:
        nxt, err = step.next_step, step.failure_step

        def key(target):
            if target is None:
                return ""
            return END if target.step_type == StepType.END else target.code

        specs.append(StepSpec(key=step.code, name=step.name, next=key(nxt), on_error=key(err)))
    errors = validate_graph(specs)
    if errors:
        raise ValidationError(errors)


# --------------------------------------------------------------------- preview
@dataclass
class PreviewStep:
    """Duck-types the attributes ``offerings/_steps.html`` reads from a step."""

    code: str
    name: str
    step_type: str
    order: int
    responsible_role: object = None
    sla_minutes: int | None = None
    effective_offering: object = None
    is_delegated: bool = False
    is_external: bool = False
    message: str = ""
    next_step: object = None
    failure_step: object = None
    jump_to: object = None

    def get_step_type_display(self):
        return StepType(self.step_type).label


def preview_steps(specs, own_offering):
    """Unsaved step objects (incl. Início/Fim) for the live preview card."""
    start = PreviewStep(START, _("Start"), StepType.START, 10)
    end = PreviewStep(END, _("End"), StepType.END, 20 + len(specs) * 10)
    by_key = {END: end}
    items = []
    for index, spec in enumerate(specs):
        responsible = spec.service_offering or own_offering
        item = PreviewStep(
            code=spec.key,
            name=spec.name or "—",
            step_type=spec.step_type,
            order=20 + index * 10,
            responsible_role=spec.role,
            sla_minutes=spec.sla_minutes,
            effective_offering=responsible,
            is_delegated=spec.service_offering is not None,
            is_external=spec.service_offering is not None,
            message=spec.message,
        )
        by_key[spec.key] = item
        items.append(item)
    start.next_step = items[0] if items else end
    for spec, item in zip(specs, items, strict=False):
        item.next_step = by_key.get(spec.next)
        item.failure_step = by_key.get(spec.on_error)
    return annotate_flow([start, *items, end])


def annotate_flow(steps):
    """Set ``jump_to`` on steps whose normal next step is not the one listed
    right after them, so the card can show non-linear flow."""
    steps = list(steps)
    for index, step in enumerate(steps):
        following = steps[index + 1] if index + 1 < len(steps) else None
        nxt = step.next_step
        step.jump_to = (
            nxt if nxt is not None and (following is None or nxt.code != following.code) else None
        )
    return steps
