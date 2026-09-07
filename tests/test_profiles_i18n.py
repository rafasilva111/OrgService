"""Per-user language preference (profiles app) and UI internationalization.

Covers the Settings form (identity + interface language), the read-only
My profile page, anonymous redirects, and the middleware that re-activates
the stored language on every request so the choice follows the account
across sessions.
"""

import pytest
from django.urls import reverse
from django.utils import translation

from apps.profiles.models import UserProfile

pytestmark = pytest.mark.django_db

SETTINGS_URL = reverse("profiles:settings")
PROFILE_URL = reverse("profiles:profile")
LOGIN_URL = "/login/"


@pytest.fixture(autouse=True)
def _reset_active_language():
    """Avoid leaking the active language between requests/tests."""
    translation.deactivate_all()
    yield
    translation.deactivate_all()


def _login(client, user, password="x"):
    assert client.login(username=user.username, password=password)


# ---------------------------------------------------------------------------
# Anonymous redirect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [PROFILE_URL, SETTINGS_URL])
def test_anonymous_redirect(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert response.url.startswith(LOGIN_URL)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_profile_page_renders(client, make_user):
    user = make_user()
    _login(client, user)
    response = client.get(PROFILE_URL)
    assert response.status_code == 200
    body = response.content.decode()
    assert "My profile" in body
    assert user.username in body
    assert SETTINGS_URL in body


def test_settings_page_renders_with_language_options(client, make_user):
    user = make_user()
    _login(client, user)
    response = client.get(SETTINGS_URL)
    assert response.status_code == 200
    body = response.content.decode()
    assert "Platform default" in body
    assert "Português" in body
    assert "English" in body


# ---------------------------------------------------------------------------
# Saving identity + language
# ---------------------------------------------------------------------------


def test_settings_post_updates_identity_and_language(client, make_user):
    user = make_user()
    _login(client, user)
    response = client.post(
        SETTINGS_URL,
        {
            "first_name": "Ana",
            "last_name": "Silva",
            "email": "ana@example.com",
            "language": "pt",
        },
    )
    assert response.status_code == 302
    assert response.url == SETTINGS_URL

    user.refresh_from_db()
    assert (user.first_name, user.last_name, user.email) == (
        "Ana",
        "Silva",
        "ana@example.com",
    )

    profile = UserProfile.objects.get(user=user)
    assert profile.language == "pt"

    # A stored preference also applies to non-settings pages.
    response = client.get(PROFILE_URL)
    body = response.content.decode()
    assert "O meu perfil" in body
    assert "Idioma da interface" in body


def test_settings_post_with_empty_language_clears_preference(client, make_user):
    user = make_user()
    _login(client, user)
    client.post(
        SETTINGS_URL,
        {"first_name": "", "last_name": "", "email": "", "language": "pt"},
    )
    UserProfile.objects.update(language="")  # simulate clearing
    response = client.post(
        SETTINGS_URL,
        {"first_name": "", "last_name": "", "email": "", "language": ""},
    )
    assert response.status_code == 302
    profile = UserProfile.objects.get(user=user)
    assert profile.language == ""
    body = client.get(SETTINGS_URL).content.decode()
    assert "Platform default" in body


# ---------------------------------------------------------------------------
# Default language is English, middleware overrides with stored language
# ---------------------------------------------------------------------------


def test_english_is_default_without_preference(client, make_user):
    user = make_user()
    _login(client, user)
    body = client.get(SETTINGS_URL).content.decode()
    assert "Save changes" in body
    assert "Guardar alterações" not in body


def test_middleware_reactivates_stored_language(client, make_user):
    user = make_user()
    UserProfile.objects.create(user=user, language="pt")
    _login(client, user)
    body = client.get(SETTINGS_URL).content.decode()
    assert "Guardar alterações" in body
    assert "Idioma da interface" in body


def test_language_preference_follows_account_across_sessions(client, make_user):
    user = make_user()
    _login(client, user)
    client.post(
        SETTINGS_URL,
        {"first_name": "", "last_name": "", "email": "", "language": "pt"},
    )
    client.logout()

    assert client.login(username=user.username, password="x")
    body = client.get(SETTINGS_URL).content.decode()
    assert "Guardar alterações" in body
