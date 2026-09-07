"""Incidents: disruptions to services."""

from django.urls import path

from . import views

app_name = "incidents"

urlpatterns = [
    path("", views.IncidentListView.as_view(), name="index"),
    path("new/", views.IncidentCreateView.as_view(), name="create"),
    path("<int:pk>/", views.IncidentDetailView.as_view(), name="detail"),
]
