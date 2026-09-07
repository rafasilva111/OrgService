"""Audit: compliance log."""

from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.AuditLogView.as_view(), name="log"),
]
