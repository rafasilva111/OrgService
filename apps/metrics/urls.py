"""Metrics & KPIs."""

from django.urls import path

from . import views

app_name = "metrics"

urlpatterns = [
    path("", views.MetricListView.as_view(), name="index"),
    path("kpis/", views.KpiListView.as_view(), name="kpis"),
    path("<int:pk>/", views.MetricDetailView.as_view(), name="detail"),
]
