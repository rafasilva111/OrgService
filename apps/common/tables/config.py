"""Declarative table configuration (apex-inspired).

Pure data, no rendering logic — ``TableView`` (views.py) interprets these
dataclasses; the templates under ``templates/core/tables/`` render them.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


class FilterKind(str):
    """Kinds of column filter supported by the table layer."""

    TEXT = "text"
    CHOICE = "choice"
    DATERANGE = "daterange"


@dataclass
class Filter:
    """Filter definition for a single column.

    ``param`` overrides the query-parameter name (defaults to the column key).
    ``lookup`` overrides the ORM lookup path used to filter (defaults to the
    column key translated to ``__`` form) — e.g. a "Service" column keyed on
    ``service.name`` (for display/sort/search) can filter by ``service_id``
    instead, so a dropdown of services filters by an exact, unambiguous pk
    rather than a name substring/exact match.
    ``choices_callable`` resolves choices dynamically per-request (e.g. the
    services the current user may see) — takes the ``TableView`` instance and
    returns ``[(value, label), ...]``. Takes precedence over ``choices``.
    Daterange filters use ``<param>__from`` and ``<param>__to``.
    """

    kind: str
    label: str | None = None
    choices: list[tuple[str, str]] | None = None
    choices_callable: Callable[[Any], list[tuple[str, str]]] | None = None
    placeholder: str = ""
    param: str | None = None
    lookup: str | None = None

    def resolved_param(self, column_key: str) -> str:
        return self.param or column_key

    def resolved_lookup(self, column_key: str) -> str:
        return self.lookup or column_key.replace(".", "__")

    def resolved_choices(self, view: Any = None) -> list[tuple[str, str]]:
        if self.choices_callable is not None:
            return list(self.choices_callable(view))
        return self.choices or []


@dataclass
class Column:
    key: str
    label: str
    sortable: bool = False
    searchable: bool = False
    filter: Filter | None = None
    template: str | None = None  # cell fragment under templates/
    width: str | None = None

    def resolved_filter(self) -> Filter | None:
        if self.filter is None:
            return None
        if self.filter.label is None:
            self.filter.label = self.label
        return self.filter


@dataclass
class BulkAction:
    """A non-destructive batch operation offered by a table."""

    slug: str
    label: str
    icon: str = "check"
    confirm_text: str = ""
    destructive: bool = False


@dataclass
class TableConfig:
    key: str
    columns: list[Column]
    bulk_actions: list[BulkAction] = field(default_factory=list)
    default_sort: str | None = None
    page_size: int = 25
    caption: str = ""
    empty_headline: str = "Nothing here yet"
    empty_body: str = ""
    searchable: bool = True
    row_url_name: str | None = None  # reverse() name of the detail view
