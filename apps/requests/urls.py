"""Requests: demand intake into services."""

from django.urls import path

from . import views

app_name = "requests"

urlpatterns = [
    path("", views.RequestListView.as_view(), name="index"),
    path("new/", views.RequestCreateView.as_view(), name="create"),
    path("new/offerings/", views.RequestOfferingOptionsView.as_view(), name="offering_options"),
    path("new/steps/", views.RequestOfferingStepsView.as_view(), name="offering_steps"),
    path("<int:pk>/", views.RequestDetailView.as_view(), name="detail"),
]
