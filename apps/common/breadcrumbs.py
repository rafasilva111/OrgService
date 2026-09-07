"""Breadcrumb support for class-based views (apex-inspired)."""

from typing import Any

from django.urls import reverse


class BreadcrumbsMixin:
    """Injects a ``breadcrumbs`` context variable.

    Views declare:
      - breadcrumb_title: str                 (or override get_breadcrumb_title)
      - breadcrumb_parent: str | tuple[str, str] | None
          URL name of the parent view, or (title, url_name) when the label
          differs from the navigation label.
    """

    breadcrumb_title: str | None = None
    breadcrumb_parent: str | tuple[str, str] | None = None

    def get_breadcrumb_title(self) -> str:
        return self.breadcrumb_title or ""

    @staticmethod
    def _resolve_parent_title(url_name: str) -> str:
        from apps.common.navigation import NAV_ITEMS

        for item in NAV_ITEMS:
            if item.url_name == url_name:
                return item.label
        return url_name.split(":")[-1].replace("_", " ").title()

    def get_breadcrumbs(self) -> list[tuple[str, str | None]]:
        crumbs: list[tuple[str, str | None]] = [("Dashboard", reverse("dashboard"))]
        parent = self.breadcrumb_parent
        if parent:
            if isinstance(parent, tuple):
                title, url_name = parent
            else:
                title, url_name = self._resolve_parent_title(parent), parent
            crumbs.append((title, reverse(url_name)))
        crumbs.append((self.get_breadcrumb_title(), None))
        return crumbs

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs) if hasattr(super(), "get_context_data") else {}
        ctx["breadcrumbs"] = self.get_breadcrumbs()
        return ctx
