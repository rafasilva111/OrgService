"""People: actors that participate in the platform.

Authentication identity (``auth.User``) is deliberately kept separate from the
domain ``Person`` — an actor can be an employee, contractor, supplier, citizen
or partner, and may not even have a login account yet.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import ActiveModel, SoftDeleteModel, TimeStampedModel


class PersonType(models.TextChoices):
    EMPLOYEE = "EMPLOYEE", _("Employee")
    CONTRACTOR = "CONTRACTOR", _("Contractor")
    EXTERNAL_SUPPLIER = "EXTERNAL_SUPPLIER", _("External Supplier")
    CITIZEN = "CITIZEN", _("Citizen")
    PARTNER = "PARTNER", _("Partner")


class Person(SoftDeleteModel, ActiveModel):
    """An actor participating in the organization (not necessarily an employee)."""

    # Authentication identity — optional so we can model people without accounts.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="person",
        help_text=_("Optional login identity for this person."),
    )
    full_name = models.CharField(_("full name"), max_length=255)
    email = models.EmailField(_("email"), null=True, blank=True, db_index=True)
    phone = models.CharField(_("phone"), max_length=32, null=True, blank=True)
    person_type = models.CharField(
        _("person type"),
        max_length=32,
        choices=PersonType.choices,
        default=PersonType.CITIZEN,
        db_index=True,
    )

    class Meta:
        verbose_name = _("person")
        verbose_name_plural = _("people")
        indexes = [
            models.Index(fields=["person_type", "active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(email__isnull=False),
                name="u_person_email",
            ),
        ]

    def __str__(self):
        return self.full_name


class Skill(TimeStampedModel):
    """Reusable capability/qualification (e.g. "Vehicle Operator")."""

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128, unique=True)
    description = models.TextField(_("description"), blank=True, default="")

    class Meta:
        verbose_name = _("skill")
        verbose_name_plural = _("skills")

    def __str__(self):
        return self.name


class SkillLevel(models.TextChoices):
    BASIC = "BASIC", _("Basic")
    INTERMEDIATE = "INTERMEDIATE", _("Intermediate")
    ADVANCED = "ADVANCED", _("Advanced")
    EXPERT = "EXPERT", _("Expert")


class PersonSkill(TimeStampedModel):
    """Associates a person with a skill they hold."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="skills")
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="person_links")
    level = models.CharField(
        _("level"), max_length=16, choices=SkillLevel.choices, default=SkillLevel.BASIC
    )
    verified_by = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_skills",
        help_text=_("Person that certified this skill."),
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)

    class Meta:
        verbose_name = _("person skill")
        verbose_name_plural = _("person skills")
        constraints = [
            models.UniqueConstraint(fields=["person", "skill"], name="u_person_skill"),
        ]

    def __str__(self):
        return f"{self.person} – {self.skill} ({self.level})"
