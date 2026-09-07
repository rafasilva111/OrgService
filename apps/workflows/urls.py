"""Workflows: definitions and executions."""

from django.urls import path

from . import views

app_name = "workflows"

urlpatterns = [
    path("", views.WorkflowListView.as_view(), name="index"),
    path("executions/", views.WorkflowExecutionListView.as_view(), name="executions"),
    path("<int:pk>/", views.WorkflowDetailView.as_view(), name="detail"),
]
