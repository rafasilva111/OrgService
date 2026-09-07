"""Request-aware audit capture.

Records an ``AuditLog`` row whenever a core catalog record is created or
updated *through an authenticated HTTP request*. Changes made from management
commands (seeding), the shell or background jobs (where there is no active
request) are deliberately not audited here — they run without a request context
(a ``None`` actor) and are excluded to keep seeds cheap and idempotent.

Works by capturing the pre-save state on ``pre_save`` and writing the log on
``post_save``. Old/new values are stored as simple JSON-friendly dicts of the
record's concrete fields (excludes internals and foreign keys that would pull
unrelated queries).
"""

import threading

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_save

from apps.common.models import TimeStampedModel

_state = threading.local()


class _RequestContext:
    __slots__ = ("user", "created")


def get_current_user() -> object | None:
    """The authenticated user for the current request, or ``None``."""
    ctx = getattr(_state, "request", None)
    user = getattr(ctx, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def set_current_user(user):
    _state.request = user


def clear_current_user():
    _state.request = None


def _field_dataset(instance):
    """Plain JSON-friendly snapshot of the record's own concrete fields."""
    data = {}
    for field in instance._meta.concrete_fields:
        if getattr(field, "auto_created", False) or field.name in (
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "password",
        ):
            continue
        try:
            value = getattr(instance, field.name)
        except Exception:
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            data[field.name] = value
            continue
        # Foreign keys: store the pk, not the related object.
        if getattr(field, "is_relation", False):
            data[field.name] = getattr(value, "pk", None)
    return data


# ---------------------------------------------------------------------------
# Signals on the core catalog models (Organization, Service, Person,
# Resource, Role). Adding more models = registering them in the tuple below.
# ---------------------------------------------------------------------------


def _register(model_name):
    model = None
    try:
        from django.apps import apps

        model = apps.get_model(model_name)
    except LookupError:
        return None
    return model


_CATALOG_MODELS = (
    "organizations.Organization",
    "services.Service",
    "people.Person",
    "resources.Resource",
    "access.Role",
)


def _pre_save(sender, instance, **kwargs):
    if isinstance(instance, TimeStampedModel):
        # Capture pre-save values for update detection.
        state = _snapshot(instance)
        _state.copy = state
    else:
        _state.copy = None


def _snapshot(instance):
    return _field_dataset(instance)


def _post_save(sender, instance, created, **kwargs):
    user = get_current_user()
    if user is None:
        _state.copy = None
        return
    old = getattr(_state, "copy", None) or {}
    new = _field_dataset(instance)
    _state.copy = None

    from apps.audit.models import AuditLog
    from apps.people.models import Person

    actor_person = Person.objects.filter(user=user).first()
    org_id = getattr(instance, "_audit_org_id", None)
    service_id = getattr(instance, "_audit_service_id", None)

    # Denormalized org/service scope when the model exposes it.
    for attr, store in (("organization_id", "organization"), ("service_id", "service")):
        if getattr(instance, attr, None) and not (
            store == "organization" and getattr(instance, "_audit_org_id", None)
        ):
            try:
                val = getattr(instance, attr)
                if val is not None:
                    if store == "organization":
                        org_id = val
                    elif store == "service":
                        service_id = val
            except Exception:
                pass

    AuditLog.objects.create(
        action=f"{instance._meta.model_name}.{'created' if created else 'updated'}",
        actor_person=actor_person,
        actor_user=user if user.is_authenticated else None,
        organization_id=org_id,
        service_id=service_id,
        object_type=ContentType.objects.get_for_model(instance),
        object_id=instance.pk,
        old_value=(old if not created else None),
        new_value=new,
    )


def connect():
    for label in _CATALOG_MODELS:
        model = _register(label)
        if model is None:
            continue
        if not getattr(model, "_AUDIT_SIGNAL_CONNECTED", False):
            pre_save.connect(_pre_save, sender=model, weak=False)
            post_save.connect(_post_save, sender=model, weak=False)
            model._AUDIT_SIGNAL_CONNECTED = True
