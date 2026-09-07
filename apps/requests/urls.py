"""Requests: demand intake into services."""

from django.urls import path

from . import views

app_name = "requests"

urlpatterns = [
    path("", views.RequestListView.as_view(), name="index"),
    path("new/", views.RequestCreateView.as_view(), name="create"),
    path("<int:pk>/", views.RequestDetailView.as_view(), name="detail"),
]
