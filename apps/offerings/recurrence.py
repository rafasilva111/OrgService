"""Recurring schedules for EVENT offerings: a readable summary and the next dates.

Weekdays use Python's numbering (0 = Monday … 6 = Sunday).
"""

import calendar
from datetime import date, timedelta

from django.utils.dates import MONTHS, WEEKDAYS
from django.utils.formats import date_format
from django.utils.translation import get_language, ngettext
from django.utils.translation import gettext as _

MAX_SCAN = 5000  # safety bound for occurrence generation


def _clamp_day(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _name(label):
    """Day/month names are lowercase in Portuguese (Django's catalog capitalises them)."""
    text = str(label)
    return text.lower() if (get_language() or "").startswith("pt") else text


def _weekday_names(days):
    return ", ".join(_name(WEEKDAYS[d]) for d in sorted(set(days)) if d in WEEKDAYS)


def summary(offering):
    freq = offering.recurrence_frequency
    n = max(offering.recurrence_interval or 1, 1)
    day = offering.recurrence_month_day
    if freq == "DAILY":
        text = ngettext("Every day", "Every %(n)s days", n) % {"n": n}
    elif freq == "WEEKLY":
        text = ngettext("Every week", "Every %(n)s weeks", n) % {"n": n}
        if offering.recurrence_weekdays:
            text += " " + _("on %(days)s") % {"days": _weekday_names(offering.recurrence_weekdays)}
    elif freq == "MONTHLY":
        text = ngettext("Every month", "Every %(n)s months", n) % {"n": n}
        if day:
            text += " " + _("on day %(day)s") % {"day": day}
    elif freq == "YEARLY":
        text = ngettext("Every year", "Every %(n)s years", n) % {"n": n}
        if day and offering.recurrence_month in MONTHS:
            text += " " + _("on %(day)s %(month)s") % {
                "day": day,
                "month": _name(MONTHS[offering.recurrence_month]),
            }
    else:
        return ""
    if offering.recurrence_start:
        text += ", " + _("starting %(date)s") % {
            "date": date_format(offering.recurrence_start, "SHORT_DATE_FORMAT")
        }
    if offering.recurrence_end:
        text += ", " + _("until %(date)s") % {
            "date": date_format(offering.recurrence_end, "SHORT_DATE_FORMAT")
        }
    return text


def _candidates(offering, after=None):
    """All dates of the schedule from its start, in order (unbounded)."""
    start = offering.recurrence_start
    freq = offering.recurrence_frequency
    n = max(offering.recurrence_interval or 1, 1)
    after = max(after or start, start)
    if freq == "DAILY":
        current = start + timedelta(days=((after - start).days // n) * n)
        while True:
            yield current
            if (date.max - current).days < n:
                break
            current += timedelta(days=n)
    elif freq == "WEEKLY":
        days = sorted(set(offering.recurrence_weekdays or [start.weekday()]))
        week = start - timedelta(days=start.weekday())  # Monday of the start week
        week += timedelta(weeks=((after - week).days // 7 // n) * n)
        while True:
            for d in days:
                if (date.max - week).days < d:
                    return
                candidate = week + timedelta(days=d)
                if candidate >= start:
                    yield candidate
            if (date.max - week).days < 7 * n:
                break
            week += timedelta(weeks=n)
    elif freq == "MONTHLY":
        day = offering.recurrence_month_day or start.day
        year, month = start.year, start.month
        elapsed = (after.year - year) * 12 + after.month - month
        month += (elapsed // n) * n
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
        while year <= date.max.year:
            candidate = _clamp_day(year, month, day)
            if candidate >= start:
                yield candidate
            month += n
            year += (month - 1) // 12
            month = (month - 1) % 12 + 1
    elif freq == "YEARLY":
        day = offering.recurrence_month_day or start.day
        month = offering.recurrence_month or start.month
        year = start.year + ((after.year - start.year) // n) * n
        while year <= date.max.year:
            candidate = _clamp_day(year, month, day)
            if candidate >= start:
                yield candidate
            year += n


def occurrences_between(offering, start, end):
    """Schedule dates in the half-open interval [start, end)."""
    if not offering.recurrence_frequency or not offering.recurrence_start or start >= end:
        return []
    result = []
    for candidate in _candidates(offering, after=start):
        if candidate >= end or (offering.recurrence_end and candidate > offering.recurrence_end):
            break
        if candidate >= start:
            result.append(candidate)
    return result


def occurrences(offering, count=3, after=None):
    """The next ``count`` dates on or after ``after`` (default: today)."""
    if not offering.recurrence_frequency or not offering.recurrence_start:
        return []
    after = after or date.today()
    end = offering.recurrence_end
    out = []
    for scanned, candidate in enumerate(_candidates(offering)):
        if scanned > MAX_SCAN or (end and candidate > end):
            break
        if candidate >= after:
            out.append(candidate)
            if len(out) == count:
                break
    return out
