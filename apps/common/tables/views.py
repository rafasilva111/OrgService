"""ListView base that turns a declarative ``TableConfig`` into a full table.

Responsibilities handled here (all keyword flavoured like apex):
  * global search (``?q=``) across ``searchable`` columns;
  * per-column filters (text / choice / daterange) via ``?key=value``;
  * sorting via ``?sort=key`` or ``?sort=-key``;
  * pagination from the config;
  * HTMX partial rendering (``HX-Request`` or ``?partial=table``);
  * a read-mostly bulk-action hook (POST ``action`` + ``ids``).

Authz scoping stays in the subclass — override ``get_base_queryset()`` to
return ``Model.objects.visible_to(user, action)`` (see apps.common.authorization).
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.views.generic import ListView

from .config import FilterKind, TableConfig


def db_key(key: str) -> str:
    """Translate a display path (``person.full_name``) to a DB lookup path
    (``person__full_name``) for search/filter/sort."""
    return key.replace(".", "__")


class TableView(ListView):
    table_config: TableConfig | None = None
    partial_template_name = "core/tables/_table.html"

    # ``?service=<pk>`` (repeatable) restricts rows to specific service(s) —
    # distinct from the free-text ``service.name`` column filter, this is an
    # exact-match link used by dashboard "drill down" stat cards. Subclasses
    # set this to the ORM lookup path to the service FK (e.g. ``"service"``
    # or ``"workflow__service"``) to enable it.
    service_filter_field: str | None = None

    # -- config -----------------------------------------------------------

    def get_table_config(self) -> TableConfig:
        assert self.table_config is not None, "TableView requires table_config"
        return self.table_config

    def get_base_queryset(self):
        """Rows to serve — subclasses apply ``visible_to`` scoping here."""
        return super().get_queryset()

    # -- search / filters / sort ------------------------------------------

    def get_search_fields(self) -> list[str]:
        return [db_key(c.key) for c in self.get_table_config().columns if c.searchable]

    def apply_service_filter(self, qs):
        if not self.service_filter_field:
            return qs
        raw_ids = [v for v in self.request.GET.getlist("service") if v.strip()]
        if not raw_ids:
            return qs
        try:
            pks = [int(v) for v in raw_ids]
        except ValueError:
            return qs
        return qs.filter(**{f"{self.service_filter_field}_id__in": pks})

    def apply_global_search(self, qs):
        q = self.request.GET.get("q", "").strip()
        fields = self.get_search_fields()
        if q and fields:
            query = Q()
            for f in fields:
                query |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(query)
        return qs, q

    def apply_column_filters(self, qs):
        config = self.get_table_config()
        active: dict[str, str] = {}
        for column in config.columns:
            filt = column.resolved_filter()
            if filt is None:
                continue
            param = filt.resolved_param(column.key)
            lookup = filt.resolved_lookup(column.key)
            if filt.kind == FilterKind.TEXT:
                value = self.request.GET.get(param, "").strip()
                if value:
                    qs = qs.filter(**{f"{lookup}__icontains": value})
                    active[param] = value
            elif filt.kind == FilterKind.CHOICE:
                value = self.request.GET.get(param, "").strip()
                if value:
                    qs = qs.filter(**{lookup: value})
                    active[param] = value
            elif filt.kind == FilterKind.DATERANGE:
                d_from = self.request.GET.get(f"{param}__from", "").strip()
                d_to = self.request.GET.get(f"{param}__to", "").strip()
                if d_from:
                    parsed = parse_date(d_from)
                    if parsed:
                        qs = qs.filter(**{f"{lookup}__gte": parsed})
                        active[f"{param}__from"] = d_from
                if d_to:
                    parsed = parse_date(d_to)
                    if parsed:
                        qs = qs.filter(**{f"{lookup}__lte": parsed})
                        active[f"{param}__to"] = d_to
        return qs, active

    def apply_sort(self, qs):
        config = self.get_table_config()
        sort = self.request.GET.get("sort", "").strip()
        sort_key = sort or config.default_sort or ""
        if sort_key:
            stripped = sort_key.lstrip("-")
            column = next((c for c in config.columns if c.key == stripped), None)
            if column is not None and column.sortable:
                qs = qs.order_by(db_key(sort_key))
        return qs, sort_key

    def get_queryset(self):
        qs = self.get_base_queryset()
        qs = self.apply_service_filter(qs)
        qs, self.active_q = self.apply_global_search(qs)
        qs, self.active_filters = self.apply_column_filters(qs)
        qs, self.sort_key = self.apply_sort(qs)
        return qs

    # -- pagination -------------------------------------------------------

    def get_paginate_by(self, queryset):
        return self.get_table_config().page_size or None

    # -- bulk actions (POST) ----------------------------------------------

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            return self.handle_post(request)
        return super().dispatch(request, *args, **kwargs)

    def handle_post(self, request):
        action = request.POST.get("action", "")
        ids = request.POST.getlist("ids")
        available = {a.slug: a for a in self.get_table_config().bulk_actions}
        if not action or action not in available:
            raise PermissionDenied
        queryset = self.get_base_queryset().filter(id__in=ids)
        return self.perform_bulk_action(action, queryset)

    def perform_bulk_action(self, action: str, queryset):
        """Subclasses implement semantics; default refuses unknown actions."""
        raise PermissionDenied

    # -- rendering --------------------------------------------------------

    def is_partial_request(self) -> bool:
        return bool(
            self.request.headers.get("HX-Request") or self.request.GET.get("partial") == "table"
        )

    def get_partial_template_name(self) -> str:
        return self.partial_template_name

    def render_to_response(self, context, **response_kwargs):
        if self.is_partial_request():
            template = self.get_partial_template_name()
            context["partial"] = True
            return self.response_class(
                request=self.request,
                template=template,
                context=context,
                using=self.template_engine,
                **response_kwargs,
            )
        return super().render_to_response(context, **response_kwargs)

    def build_table_filters(self) -> list[dict]:
        """Render-ready descriptions for the filter drawer (_filters.html)."""
        config = self.get_table_config()
        active = getattr(self, "active_filters", {})
        rows: list[dict] = []
        for column in config.columns:
            filt = column.resolved_filter()
            if filt is None:
                continue
            param = filt.resolved_param(column.key)
            row = {
                "label": filt.label or column.label,
                "kind": filt.kind,
                "param": param,
                "param_from": f"{param}__from",
                "param_to": f"{param}__to",
                "choices": filt.resolved_choices(self),
                "placeholder": filt.placeholder,
                "value": "",
                "value_from": "",
                "value_to": "",
            }
            if filt.kind == FilterKind.DATERANGE:
                row["value_from"] = active.get(f"{param}__from", "")
                row["value_to"] = active.get(f"{param}__to", "")
            else:
                row["value"] = active.get(param, "")
            rows.append(row)
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config = self.get_table_config()
        ctx["config"] = config
        ctx["visible_columns"] = config.columns
        ctx["active_q"] = getattr(self, "active_q", "")
        ctx["active_filters"] = getattr(self, "active_filters", {})
        ctx["sort_key"] = getattr(self, "sort_key", config.default_sort or "")
        # Query string without sort/page, so sort links preserve q & filters.
        params = {k: v for k, v in self.request.GET.items() if k not in ("sort", "page")}
        ctx["query_without_sort"] = urlencode(params, doseq=True)
        ctx["has_filters"] = any(c.filter for c in config.columns)
        ctx["has_search"] = bool(config.searchable and self.get_search_fields())
        ctx["table_filters"] = self.build_table_filters()
        return ctx
