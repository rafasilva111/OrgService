"""Changes: controlled changes to a service or organization.

A change may target a service OR an organization — service is preferred when a
single service is touched; organization-level changes have ``organization`` set
and ``service`` null.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import TimeStampedModel


class ChangeStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    PROPOSED = "PROPOSED", _("Proposed")
    APPROVED = "APPROVED", _("Approved")
    IN_PROGRESS = "IN_PROGRESS", _("In Progress")
    IMPLEMENTED = "IMPLEMENTED", _("Implemented")
    VERIFIED = "VERIFIED", _("Verified")
    REJECTED = "REJECTED", _("Rejected")
    ROLLED_BACK = "ROLLED_BACK", _("Rolled Back")


class RiskLevel(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")


class Change(TimeStampedModel):
    """A controlled, authorized modification."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    service = models.ForeignKey(
        "services.Service",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="changes",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="changes",
        help_text=_("Used when the change targets the organization itself."),
    )
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    reason = models.TextField(_("reason"), blank=True, default="")
    risk = models.CharField(
        _("risk"), max_length=16, choices=RiskLevel.choices, default=RiskLevel.MEDIUM
    )
    status = models.CharField(
        _("status"), max_length=16, choices=ChangeStatus.choices, default=ChangeStatus.DRAFT
    )
    requested_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="changes_requested",
    )
    planned_start = models.DateTimeField(_("planned start"), null=True, blank=True)
    planned_end = models.DateTimeField(_("planned end"), null=True, blank=True)

    class Meta:
        verbose_name = _("change")
        verbose_name_plural = _("changes")
        indexes = [
            models.Index(fields=["service", "status"]),
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["risk", "status"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"


class ApprovalDecision(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")


class ChangeApproval(TimeStampedModel):
    """Required approval before a change proceeds."""

    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name="approvals")
    approver = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="change_approvals",
    )
    # Role that authorizes this kind of change (e.g. EXECUTIVE).
    role = models.ForeignKey(
        "access.Role",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="change_approvals",
    )
    decision = models.CharField(
        _("decision"),
        max_length=16,
        choices=ApprovalDecision.choices,
        default=ApprovalDecision.PENDING,
    )
    comment = models.TextField(_("comment"), blank=True, default="")
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)

    class Meta:
        verbose_name = _("change approval")
        verbose_name_plural = _("change approvals")
        constraints = [
            models.UniqueConstraint(fields=["change", "approver"], name="u_change_approver"),
        ]

    def __str__(self):
        return f"Approval for #{self.change_id} ({self.decision})"
