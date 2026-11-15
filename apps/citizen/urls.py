"""Citizen portal URLs (``citizen:``), mounted at ``/citizen/``."""

from django.urls import path

from . import views

app_name = "citizen"

urlpatterns = [
    path("", views.CitizenHomeView.as_view(), name="home"),
    path("login/", views.CitizenLoginView.as_view(), name="login"),
    path("logout/", views.CitizenLogoutView.as_view(), name="logout"),
    path("signup/", views.CitizenSignupView.as_view(), name="signup"),
    path("profile/", views.CitizenProfileView.as_view(), name="profile"),
    path("services/", views.CitizenServiceListView.as_view(), name="services"),
    path("services/<int:pk>/", views.CitizenServiceDetailView.as_view(), name="service_detail"),
    path("offerings/<int:pk>/", views.CitizenOfferingDetailView.as_view(), name="offering_detail"),
    path("requests/", views.CitizenRequestListView.as_view(), name="requests"),
    path("requests/new/", views.CitizenRequestCreateView.as_view(), name="request_create"),
    path("requests/<int:pk>/", views.CitizenRequestDetailView.as_view(), name="request_detail"),
    path(
        "requests/<int:pk>/attachments/<int:attachment_pk>/",
        views.CitizenAttachmentView.as_view(),
        name="attachment",
    ),
]
