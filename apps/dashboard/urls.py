"""Platform dashboards: executive overview + per-organization/service pages.

Dashboard urls are name-spaced with ``app_name = "dashboard"`` and mounted at
the root (see config/urls.py), so ``reverse("dashboard")`` is the home page and
the login/logout endpoints live at ``/login/`` and ``/logout/``.
"""

from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("login/", views.PlatformLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path(
        "organization/<int:pk>/",
        views.OrganizationDashboard.as_view(),
        name="organization_dashboard",
    ),
    path(
        "service/<int:pk>/",
        views.ServiceDashboard.as_view(),
        name="service_dashboard",
    ),
    path(
        "service-context/",
        views.SetServiceContextView.as_view(),
        name="set_service_context",
    ),
    path(
        "organization-context/",
        views.SetOrganizationContextView.as_view(),
        name="set_org_context",
    ),
    path("", views.ExecutiveDashboard.as_view(), name="dashboard"),
]
