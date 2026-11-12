"""Service Offerings: definitions and executions."""

from django.urls import path

from . import views

app_name = "offerings"

urlpatterns = [
    path("", views.ServiceOfferingListView.as_view(), name="index"),
    path("new/", views.OfferingBuilderView.as_view(), name="create"),
    path("preview/", views.OfferingPreviewView.as_view(), name="preview"),
    path("<int:pk>/edit/", views.OfferingBuilderView.as_view(), name="edit"),
    path("<int:pk>/publish/", views.OfferingPublishView.as_view(), name="publish"),
    path("<int:pk>/new-version/", views.OfferingNewVersionView.as_view(), name="new_version"),
    path("<int:pk>/delete/", views.OfferingDeleteDraftView.as_view(), name="delete"),
    path("executions/", views.OfferingExecutionListView.as_view(), name="executions"),
    path("<int:pk>/", views.ServiceOfferingDetailView.as_view(), name="detail"),
]
