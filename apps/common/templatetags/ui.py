"""Small UI template helpers (apex-inspired, self-contained).

Provides:
  * ``icon``        — inline SVG icons (stroke-based);
  * ``pill_class``  — Tailwind classes for status/priority badges;
  * ``fieldvalue``  — dotted-path attribute access inside table cells;
  * ``apex_field``  — consistent form-field rendering;
  * ``initials``    — avatar initials from a User.
"""

from django import forms
from django.template import Library
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = Library()


# Inline SVG bodies (heroicons-outline style, 24x24 stroke).
ICONS = {
    "dashboard": '<path d="M4 6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6zm10 0a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2V6zM4 16a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2zm10 0a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2v-2z"/>',
    "orgs": '<path d="M19 21V5a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v5m-4 0h4"/>',
    "memberships": '<path d="M17 20h5v-2a3 3 0 0 0-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 0 1 5.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 0 1 9.288 0M15 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm6 3a2 2 0 1 1-4 0 2 2 0 0 1 4 0zM7 10a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"/>',
    "services": '<path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0 1 12 2.944a11.955 11.955 0 0 1-8.618 3.04A12.02 12.02 0 0 0 3 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>',
    "requests": '<path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2m-6 9 2 2 4-4"/>',
    "incidents": '<path d="M13 10V3L4 14h7v7l9-11h-7z"/>',
    "problems": '<path d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/>',
    "changes": '<path d="M4 4v5h.582m15.356 2A8.001 8.001 0 0 0 4.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 0 1-15.357-2m15.357 2H15"/>',
    "workflows": '<path d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 1 1 0-2.684m0 2.684 6.632 3.316m-6.632-6 6.632-3.316m0 0a3 3 0 1 0 5.367-2.684 3 3 0 0 0-5.367 2.684zm0 9.316a3 3 0 1 0 5.368 2.684 3 3 0 0 0-5.368-2.684z"/>',
    "executions": '<path d="M14.752 11.168l-3.197-2.132A1 1 0 0 0 10 9.87v4.263a1 1 0 0 0 1.555.832l3.197-2.132a1 1 0 0 0 0-1.664zM21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/>',
    "resources": '<path d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>',
    "people": '<path d="M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM12 14a7 7 0 0 0-7 7h14a7 7 0 0 0-7-7z"/>',
    "skills": '<path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 1 1 7.072 0l-.548.547A3.374 3.374 0 0 0 9.38 17.96l-.548-.547a5 5 0 0 1 .548-.547z"/>',
    "events": '<path d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2z"/>',
    "metrics": '<path d="M9 19v-6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2zm0 0V9a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v10m-6 0a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2m0 0V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2z"/>',
    "kpis": '<path d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>',
    "roles": '<path d="M15 7a2 2 0 0 1 2 2m4 0a6 6 0 0 1-7.743 5.743L11 17H9v2H7v2H4a1 1 0 0 1-1-1v-2.586a1 1 0 0 1 .293-.707l5.964-5.964A6 6 0 1 1 21 9z"/>',
    "audit": '<path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0 1 12 2.944a11.955 11.955 0 0 1-8.618 3.04A12.02 12.02 0 0 0 3 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>',
    # Utility icons
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "search": '<path d="M21 21l-4.35-4.35M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0z"/>',
    "user": '<path d="M5.121 17.804A13.937 13.937 0 0 1 12 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm6 2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/>',
    "settings": '<path d="M10.343 3.94c.09-.542.56-.94 1.11-.94h1.093c.55 0 1.02.398 1.11.94l.149.894c.07.424.384.764.78.93.398.164.855.142 1.205-.108l.737-.527a1.125 1.125 0 0 1 1.45.12l.773.774c.39.389.44 1.002.12 1.45l-.527.737c-.25.35-.272.806-.108 1.205.166.395.506.71.93.78l.893.149c.543.09.94.56.94 1.11v1.093c0 .55-.397 1.02-.94 1.11l-.893.149c-.424.07-.764.384-.78.93-.164.398-.142.855.108 1.205l.527.737c.32.447.269 1.06-.12 1.45l-.773.774a1.125 1.125 0 0 1-1.45.12l-.737-.527c-.35-.25-.807-.272-1.205-.108-.395.166-.71.506-.78.93l-.149.893c-.09.543-.56.94-1.11.94h-1.093c-.55 0-1.02-.397-1.11-.94l-.149-.893c-.07-.424-.384-.764-.78-.93-.398-.164-.855-.142-1.205.108l-.737.527a1.125 1.125 0 0 1-1.45-.12l-.773-.774a1.125 1.125 0 0 1-.12-1.45l.527-.737c.25-.35.273-.806.108-1.205-.166-.395-.506-.71-.93-.78l-.893-.149c-.543-.09-.94-.56-.94-1.11v-1.093c0-.55.397-1.02.94-1.11l.893-.149c.424-.07.764-.384.78-.93.164-.398.142-.855-.108-1.205l-.527-.737a1.125 1.125 0 0 1 .12-1.45l.773-.774a1.125 1.125 0 0 1 1.45-.12l.737.527c.35.25.807.272 1.205.108.395-.166.71-.506.78-.93l.149-.893zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/>',
    "logout": '<path d="M17 16l4-4m0 0-4-4m4 4H7m6 4v1a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3h4a3 3 0 0 1 3 3v1"/>',
    "plus": '<path d="M12 4v16m8-8H4"/>',
    "x": '<path d="M6 18L18 6M6 6l12 12"/>',
    "filter": '<path d="M3 4a1 1 0 0 1 1-1h16a1 1 0 0 1 .7 1.7L15 11.4v6.6a1 1 0 0 1-.4.8l-4 3A1 1 0 0 1 9 21v-9.6L2.3 4.7A1 1 0 0 1 3 4z"/>',
    "chevron-right": '<path d="M9 5l7 7-7 7"/>',
    "chevron-down": '<path d="M19 9l-7 7-7-7"/>',
    "check": '<path d="M5 13l4 4L19 7"/>',
}


@register.simple_tag
def icon(name: str, class_name: str = "size-5"):
    body = ICONS.get(name)
    if not body:
        return ""
    return format_html(
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        'class="{}" aria-hidden="true">{}</svg>',
        class_name or "size-5",
        mark_safe(body),
    )


_PILL_CLASSES = {
    "OPERATIONAL": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "DEGRADED": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "DISRUPTED": "bg-red-50 text-red-700 ring-red-600/20",
    "PAUSED": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "RETIRED": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "NEW": "bg-blue-50 text-blue-700 ring-blue-600/20",
    "ASSESSING": "bg-violet-50 text-violet-700 ring-violet-600/20",
    "IN_PROGRESS": "bg-blue-50 text-blue-700 ring-blue-600/20",
    "INVESTIGATING": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "WAITING": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "RESOLVED": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "CLOSED": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "CANCELLED": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "OPEN": "bg-red-50 text-red-700 ring-red-600/20",
    "DIAGNOSED": "bg-violet-50 text-violet-700 ring-violet-600/20",
    "DRAFT": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "PROPOSED": "bg-violet-50 text-violet-700 ring-violet-600/20",
    "APPROVED": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "IMPLEMENTED": "bg-blue-50 text-blue-700 ring-blue-600/20",
    "VERIFIED": "bg-teal-50 text-teal-700 ring-teal-600/20",
    "REJECTED": "bg-red-50 text-red-700 ring-red-600/20",
    "ROLLED_BACK": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "ACTIVE": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "DEPRECATED": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "AVAILABLE": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "ASSIGNED": "bg-blue-50 text-blue-700 ring-blue-600/20",
    "MAINTENANCE": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "PENDING": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "RUNNING": "bg-blue-50 text-blue-700 ring-blue-600/20",
    "COMPLETED": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "FAILED": "bg-red-50 text-red-700 ring-red-600/20",
    "BLOCKED": "bg-red-50 text-red-700 ring-red-600/20",
    "DONE": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "LOW": "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
    "MEDIUM": "bg-blue-50 text-blue-700 ring-blue-600/20",
    "HIGH": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "MAJOR": "bg-red-50 text-red-700 ring-red-600/20",
    "URGENT": "bg-orange-50 text-orange-700 ring-orange-600/20",
    "CRITICAL": "bg-red-50 text-red-700 ring-red-600/20",
    "IMMEDIATE": "bg-red-50 text-red-700 ring-red-600/20",
}

_DEFAULT_PILL = "bg-zinc-100 text-zinc-600 ring-zinc-500/20"


@register.filter
def pill_class(value):
    return _PILL_CLASSES.get(str(value).strip().upper(), _DEFAULT_PILL)


@register.filter
def fieldvalue(obj, path: str):
    """Resolve a dotted attribute path (e.g. ``service.organization.name``)."""
    current = obj
    for part in str(path).split("."):
        if current is None:
            return ""
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current or ""


@register.filter
def elapsed(value, end=None):
    """Timedelta between ``value`` and ``end`` (defaults to now), as "1h 2m 3s"."""
    if not value:
        return "—"
    if end:
        current = end
    else:
        from django.utils import timezone

        current = timezone.now()
    seconds = max(int((current - value).total_seconds()), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@register.filter
def initials(user):
    if not user:
        return "?"
    name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if not name:
        name = getattr(user, "username", "") or "?"
    parts = [p for p in name.split() if p][:2]
    return "".join(p[0].upper() for p in parts) if parts else "?"


@register.simple_tag
def apex_field(bound_field, size: str = "md", **attrs):
    """Consistent stacked form-field rendering.

    Extra ``key=value`` string arguments are forwarded as widget attributes
    (e.g. ``{% apex_field form.title autocomplete="off" %}``).
    """
    base = (
        "block w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 "
        "text-sm text-zinc-900 shadow-sm outline-none transition "
        "focus:border-indigo-600 focus:ring-2 focus:ring-indigo-600/20 "
        "placeholder:text-zinc-400"
    )
    if size == "sm":
        base = base.replace("px-3 py-2", "px-2.5 py-1.5")
    if size == "lg":
        base = base.replace("px-3 py-2", "px-3.5 py-2.5")

    widget = bound_field.field.widget

    def _as_widget(overrides):
        merged = {**attrs, **overrides}
        return bound_field.as_widget(attrs=merged)

    require = marked_required(bound_field)

    if isinstance(widget, forms.CheckboxInput):
        rendered = bound_field
    elif isinstance(widget, forms.SelectMultiple):
        rendered = _as_widget({"class": base.replace("px-3 py-2", "px-3 py-2.5")})
    elif isinstance(widget, forms.Select):
        rendered = _as_widget({"class": base})
    elif isinstance(widget, forms.Textarea):
        rendered = _as_widget({"class": base + " resize-y min-h-24", "rows": 4})
    else:
        rendered = _as_widget({"class": base})

    html = format_html(
        '<div class="grid gap-1.5">{}{}{}{}</div>',
        (
            format_html(
                '<label class="text-sm font-medium text-zinc-700">{}{}</label>',
                bound_field.label,
                require,
            )
            if bound_field.label
            else ""
        ),
        rendered,
        "".join(
            format_html('<p class="text-xs text-red-600">{}</p>', err) for err in bound_field.errors
        )
        if bound_field.errors
        else "",
        format_html('<p class="text-xs text-zinc-500">{}</p>', bound_field.help_text)
        if bound_field.help_text
        else "",
    )
    return html


def marked_required(bound_field):
    if bound_field.field.required:
        return mark_safe(' <span class="text-red-500">*</span>')
    return ""


@register.filter
def has_perm(user, code):
    """``True`` when ``user`` holds ``code`` through an active membership."""
    from apps.common.mixins import user_has_permission_code

    return user_has_permission_code(user, code)


@register.filter
def get_item(mapping, key):
    """Dictionary lookup by key (``{{ dict|get_item:key }}``)."""
    if not mapping:
        return ""
    return mapping.get(key) or ""
