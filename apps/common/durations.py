"""Human-readable formatting for durations stored as whole minutes.

Used for SLA / target fields (``OfferingStep.sla_minutes``,
``SLA.target_minutes``…) so users see ``1d`` instead of ``1440 min``.
"""

from django.utils.translation import pgettext

# (minutes per unit, translation function for the short unit label)
_UNITS = (
    (60 * 24, lambda: pgettext("duration unit (days)", "d")),
    (60, lambda: pgettext("duration unit (hours)", "h")),
    (1, lambda: pgettext("duration unit (minutes)", "min")),
)


def humanize_minutes(minutes, max_parts: int = 2) -> str:
    """Format a number of minutes as a compact duration.

    ``humanize_minutes(1440) == "1d"``, ``humanize_minutes(90) == "1h 30min"``,
    ``humanize_minutes(1530) == "1d 1h"`` (truncated to ``max_parts`` units).
    Returns ``"—"`` for ``None``/empty and ``"0min"`` for zero.
    """
    if minutes in (None, ""):
        return "—"
    remaining = max(int(minutes), 0)
    parts = []
    for size, label in _UNITS:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value}{label()}")
        if len(parts) >= max_parts:
            break
    return " ".join(parts) or f"0{_UNITS[-1][1]()}"
