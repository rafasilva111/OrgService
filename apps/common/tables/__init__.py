"""apex-style table building blocks (reimplemented inside the platform).

Provides the dataclass-driven table layer used by every list view:

    TableConfig(key, columns=..., bulk_actions=..., default_sort=...)
    Column(key, label, sortable=False, searchable=False, filter=Filter(...))

combined with the ``TableView`` ListView base in ``views.py``.
"""

from .config import (
    BulkAction,
    Column,
    Filter,
    FilterKind,
    TableConfig,
)
from .views import TableView

__all__ = [
    "BulkAction",
    "Column",
    "Filter",
    "FilterKind",
    "TableConfig",
    "TableView",
]
