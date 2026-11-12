"""Reusable table filter builders shared by list views."""

from django.utils.translation import gettext_lazy as _

from apps.common.mixins import org_filter_choices

from .config import Column, Filter, FilterKind


def resolve_org_subtree_path(view, pk: str) -> str | None:
    """Resolve a selected organization pk to its ltree path.

    Delegates to the view's authorization-aware lookup so an out-of-scope pk
    yields ``None`` (filter ignored) rather than disclosing the organization.
    """
    return view.resolve_organization_path(pk)


def organization_column(
    key: str = "service.organization.name",
    *,
    lookups: tuple[str, ...] = ("service__organization__path",),
) -> Column:
    """An Organization column whose drawer filter covers a whole subtree.

    ``lookups`` names every ltree path that should match the selected node —
    OR'ed together, mirroring ``organization_filter_fields`` on the view.
    """
    return Column(
        key,
        _("Organization"),
        sortable=True,
        searchable=True,
        filter=Filter(
            FilterKind.SUBTREE,
            param="organization",
            lookups=lookups,
            choices_callable=org_filter_choices,
            subtree_resolver=resolve_org_subtree_path,
        ),
    )
