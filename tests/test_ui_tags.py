"""Template tags in apps/common/templatetags/ui.py."""

from django import forms

from apps.common.templatetags.ui import apex_field


class _Form(forms.Form):
    name = forms.CharField(required=True)


def test_apex_field_renders_errors_as_html_not_escaped_text():
    form = _Form(data={})
    assert not form.is_valid()
    html = str(apex_field(form["name"]))
    assert '<p class="text-xs text-red-600">' in html
    assert "&lt;p" not in html  # the error markup must not be escaped


def test_apex_field_still_escapes_the_error_text():
    form = _Form(data={})
    form.is_valid()
    form.add_error("name", "<b>bad</b>")
    html = str(apex_field(form["name"]))
    assert "&lt;b&gt;bad&lt;/b&gt;" in html
