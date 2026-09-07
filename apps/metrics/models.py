"""Metrics & KPIs: measurable values, observations, targets, SLAs.

Dashboards are NOT entities here — they are views over these tables plus
requests/incidents/workflows (see spec §21).
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import ActiveModel, TimeStampedModel


class AggregationType(models.TextChoices):
    SUM = "SUM", _("Sum")
    AVG = "AVG", _("Average")
    COUNT = "COUNT", _("Count")
    LAST = "LAST", _("Last value")
    MIN = "MIN", _("Minimum")
    MAX = "MAX", _("Maximum")


class Metric(TimeStampedModel):
    """Definition of a measurable value (e.g. "Requests Completed")."""

    _AUTH_ORG_FIELD = "service__organization"
    _AUTH_SERVICE_FIELD = "service"

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128)
    description = models.TextField(_("description"), blank=True, default="")
    unit = models.CharField(_("unit"), max_length=32, blank=True, default="")
    aggregation = models.CharField(
        _("aggregation"), max_length=8, choices=AggregationType.choices, default=AggregationType.SUM
    )
    service = models.ForeignKey(
        "services.Service",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="metrics",
        help_text=_("Optional scope; null means platform-wide metric."),
    )

    class Meta:
        verbose_name = _("metric")
        verbose_name_plural = _("metrics")

    def __str__(self):
        return self.code


class MetricValue(TimeStampedModel):
    """A measured value for a metric over a time window."""

    metric = models.ForeignKey(Metric, on_delete=models.CASCADE, related_name="values")
    value = models.DecimalField(_("value"), max_digits=18, decimal_places=4)
    period_start = models.DateTimeField(_("period start"), db_index=True)
    period_end = models.DateTimeField(_("period end"))
    # Optional slicing dimension, e.g. {"service": 5, "organization": 7}.
    dimension = models.JSONField(_("dimension"), default=dict, blank=True)

    class Meta:
        verbose_name = _("metric value")
        verbose_name_plural = _("metric values")
        indexes = [
            models.Index(fields=["metric", "period_start"]),
        ]

    def __str__(self):
        return f"{self.metric} = {self.value} [{self.period_start}]"


class KPI(TimeStampedModel):
    """Higher-level performance indicator aggregating several metrics.

    e.g. "Waste Management Service Health" blends SLA, incidents, requests,
    costs, capacity and satisfaction.
    """

    code = models.SlugField(_("code"), unique=True, max_length=64)
    name = models.CharField(_("name"), max_length=128)
    description = models.TextField(_("description"), blank=True, default="")
    service = models.ForeignKey(
        "services.Service", on_delete=models.CASCADE, null=True, blank=True, related_name="kpis"
    )
    target_value = models.DecimalField(
        _("target value"), max_digits=18, decimal_places=4, null=True, blank=True
    )
    tolerance = models.DecimalField(
        _("tolerance"), max_digits=18, decimal_places=4, null=True, blank=True
    )
    metrics = models.ManyToManyField(Metric, through="KpiMetric", related_name="kpis")

    class Meta:
        verbose_name = _("KPI")
        verbose_name_plural = _("KPIs")

    def __str__(self):
        return self.code


class KpiMetric(TimeStampedModel):
    """Weighted contribution of a metric to a KPI."""

    kpi = models.ForeignKey(KPI, on_delete=models.CASCADE, related_name="kpi_metrics")
    metric = models.ForeignKey(Metric, on_delete=models.CASCADE, related_name="kpi_links")
    weight = models.DecimalField(_("weight"), max_digits=6, decimal_places=3, default=1)

    class Meta:
        verbose_name = _("KPI metric")
        verbose_name_plural = _("KPI metrics")
        constraints = [
            models.UniqueConstraint(fields=["kpi", "metric"], name="u_kpi_metric"),
        ]

    def __str__(self):
        return f"{self.kpi} ← {self.metric} (×{self.weight})"


class ScopeType(models.TextChoices):
    RESPONSE = "RESPONSE", _("Response")
    RESOLUTION = "RESOLUTION", _("Resolution")


class SLA(TimeStampedModel, ActiveModel):
    """Service-level expectation.

    e.g.  High-priority request → response within 4h, resolution within 24h.
    """

    service = models.ForeignKey(
        "services.Service", on_delete=models.CASCADE, related_name="slas", null=True, blank=True
    )
    name = models.CharField(_("name"), max_length=128)
    priority = models.CharField(
        _("priority"),
        max_length=16,
        null=True,
        blank=True,
        help_text=_("Requests filtering value (optional)."),
    )
    scope = models.CharField(
        _("scope"), max_length=16, choices=ScopeType.choices, default=ScopeType.RESPONSE
    )
    target_minutes = models.PositiveIntegerField(_("target (minutes)"))

    class Meta:
        verbose_name = _("SLA")
        verbose_name_plural = _("SLAs")
        constraints = [
            models.UniqueConstraint(fields=["service", "name", "scope"], name="u_sla_name_scope"),
        ]

    def __str__(self):
        return f"{self.service} / {self.name} ({self.target_minutes}min)"
