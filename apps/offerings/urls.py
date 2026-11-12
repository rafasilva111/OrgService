"""Service Offerings: definitions and executions."""

from django.urls import path

from . import views

app_name = "offerings"

urlpatterns = [
    path("", views.ServiceOfferingListView.as_view(), name="index"),
    path("executions/", views.OfferingExecutionListView.as_view(), name="executions"),
    path("<int:pk>/", views.ServiceOfferingDetailView.as_view(), name="detail"),
]
