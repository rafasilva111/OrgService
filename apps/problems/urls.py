"""Problems: root-cause records behind recurring incidents."""

from django.urls import path

from . import views

app_name = "problems"

urlpatterns = [
    path("", views.ProblemListView.as_view(), name="index"),
    path("<int:pk>/", views.ProblemDetailView.as_view(), name="detail"),
]
