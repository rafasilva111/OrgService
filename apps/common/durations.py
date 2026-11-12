"""Human-readable formatting for durations stored as whole minutes.

Used for SLA / target fields (``OfferingStep.sla_minutes``,
``SLA.target_minutes``…) so users see ``1d`` instead of ``1440 min``.
"""

import re

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


# ------------------------------------------------------------------ parsing
_TOKEN = re.compile(r"(\d+)\s*(d|h|min|m)?\b")
_FACTORS = {"d": 24 * 60, "h": 60, "min": 1, "m": 1, None: 1}


def parse_duration(value):
    """Minutes for a typed duration such as ``"1d 16h 13m"`` (= 2413).

    Units: ``d``, ``h``, ``m``/``min``; a bare number is minutes. Case, spaces
    and commas are ignored. Returns ``None`` for blank input; raises
    ``ValueError`` for negatives, unknown text or a repeated unit. Accepts what
    ``humanize_minutes(..., max_parts=3)`` produces, so values round-trip.
    """
    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            raise ValueError("negative duration")
        return value
    text = str(value).strip().lower().replace(",", " ")
    if not text:
        return None
    total, seen, pos = 0, set(), 0
    for match in _TOKEN.finditer(text):
        if text[pos : match.start()].strip():
            raise ValueError(f"unexpected text in {value!r}")
        unit = match.group(2)
        key = "m" if unit == "min" else unit
        if key in seen:
            raise ValueError(f"unit repeated in {value!r}")
        seen.add(key)
        total += int(match.group(1)) * _FACTORS[unit]
        pos = match.end()
    if not seen or text[pos:].strip():
        raise ValueError(f"not a duration: {value!r}")
    return total


def duration_input(minutes) -> str:
    """Exact, editable form of ``minutes`` (e.g. ``"1d 16h 13min"``); ``""`` if empty."""
    if minutes in (None, ""):
        return ""
    return humanize_minutes(minutes, max_parts=3)
