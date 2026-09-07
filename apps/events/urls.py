"""Events: immutable timeline of platform activity."""

from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.EventTimelineView.as_view(), name="timeline"),
]
