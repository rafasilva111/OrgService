"""Resources: catalog, detail and assignments."""

from django.urls import path

from . import views

app_name = "resources"

urlpatterns = [
    path("", views.ResourceListView.as_view(), name="index"),
    path("assignments/", views.ResourceAssignmentListView.as_view(), name="assignments"),
    path("new/", views.ResourceCreateView.as_view(), name="create"),
    path("<int:pk>/activate/", views.ResourceActivateView.as_view(), name="activate"),
    path("<int:pk>/deactivate/", views.ResourceDeactivateView.as_view(), name="deactivate"),
    path("<int:pk>/delete/", views.ResourceDeleteView.as_view(), name="delete"),
    path("<int:pk>/", views.ResourceDetailView.as_view(), name="detail"),
]
