"""URL configuration for the Organizational Service Platform.

Every domain surface is mounted under a stable prefix; URL names are grouped
with ``app_name`` (e.g. ``requests:index``, ``services:detail``) so templates
and navigation can ``reverse()`` them.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.dashboard.views import ExecutiveDashboard

urlpatterns = [
    path("admin/", admin.site.urls),
    # Dashboards (apex-style project overviews)
    # Bare root "dashboard" (matches reverse("dashboard") used by breadcrumbs/
    # navigation); sub-pages stay namespaced as dashboard:organization_dashboard
    # and dashboard:service_dashboard.
    path("", ExecutiveDashboard.as_view(), name="dashboard"),
    path("", include("apps.dashboard.urls")),
    # Profile + settings (under the header profile dropdown)
    path("", include("apps.profiles.urls")),
    # Organizations + memberships
    path("organizations/", include("apps.organizations.urls")),
    # Services
    path("services/", include("apps.services.urls")),
    # Work
    path("requests/", include("apps.requests.urls")),
    path("incidents/", include("apps.incidents.urls")),
    path("problems/", include("apps.problems.urls")),
    path("changes/", include("apps.changes.urls")),
    # Workflows
    path("workflows/", include("apps.workflows.urls")),
    # Resources
    path("resources/", include("apps.resources.urls")),
    # People
    path("people/", include("apps.people.urls")),
    # Analytics
    path("events/", include("apps.events.urls")),
    path("metrics/", include("apps.metrics.urls")),
    # Administration
    path("access/", include("apps.access.urls")),
    path("audit/", include("apps.audit.urls")),
]

if "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
        *urlpatterns,
    ]
