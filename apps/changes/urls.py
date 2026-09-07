"""Changes: planned, coordinated service modifications."""

from django.urls import path

from . import views

app_name = "changes"

urlpatterns = [
    path("", views.ChangeListView.as_view(), name="index"),
    path("<int:pk>/", views.ChangeDetailView.as_view(), name="detail"),
]
