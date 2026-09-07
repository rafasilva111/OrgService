"""Abstract model mixins reused across the domain apps."""

from django.db import models
from django.utils import timezone

from apps.common.authorization import AuthorizationQuerySetMixin


class AuthQuerySet(AuthorizationQuerySetMixin, models.QuerySet):
    """QuerySet exposing ``.visible_to(user, action)``."""


class ActiveQuerySet(AuthQuerySet):
    """QuerySet that hides deactivated records by default.

    NOTE: intentionally conservative — the skeleton keeps ``all()`` semantics
    (no implicit filtering) so ``updated_at``/``active`` decisions stay in the
    view layer. Activation filtering can be toggled per model later.
    """


class TimeStampedModel(models.Model):
    """Common audit timestamps; also carries the authorization manager."""

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    objects = models.Manager.from_queryset(AuthQuerySet)()

    class Meta:
        abstract = True


class ActiveModel(models.Model):
    """Soft-deactivation flag. Records are never hard-deleted."""

    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(AuthQuerySet):
    """QuerySet with soft-delete helpers.

    Keeps ``all()`` semantics (no implicit filtering), consistent with
    ``ActiveQuerySet``: deleted rows remain visible at the ORM level so that
    ``get_or_create`` in the idempotent seeds keeps finding them. The view
    layer filters via ``alive()``.
    """

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def trashed(self):
        return self.filter(deleted_at__isnull=False)

    def soft_delete(self):
        return self.update(deleted_at=timezone.now())

    def restore(self):
        return self.update(deleted_at=None)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager for soft-deletable models (conservative ``all()``)."""


class SoftDeleteModel(TimeStampedModel):
    """Adds soft deletion via ``deleted_at`` to a timestamped model."""

    deleted_at = models.DateTimeField(
        default=None, null=True, blank=True, editable=False, db_index=True
    )

    objects = SoftDeleteManager()

    class Meta:
        abstract = True

    def soft_delete(self):
        if self.deleted_at is None:
            self.deleted_at = timezone.now()
            self.save(update_fields=["deleted_at"])

    def restore(self):
        if self.deleted_at is not None:
            self.deleted_at = None
            self.save(update_fields=["deleted_at"])
