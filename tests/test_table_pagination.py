import pytest
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.utils import translation


@pytest.mark.parametrize(
    ("language", "number", "expected"),
    [
        ("en", 1, "Page 1 of 5"),
        ("en", 5, "Page 5 of 5"),
        ("pt", 1, "Página 1 de 5"),
        ("pt", 5, "Página 5 de 5"),
    ],
)
def test_pagination_displays_current_and_total_pages(language, number, expected):
    paginator = Paginator(list(range(100)), 20)
    with translation.override(language):
        rendered = render_to_string(
            "core/tables/_pagination.html",
            {
                "is_paginated": True,
                "page_obj": paginator.page(number),
                "config": {"key": "test"},
            },
        )
    assert expected in rendered

