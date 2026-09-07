"""People: directory, detail and skills."""

from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("", views.PersonDirectoryView.as_view(), name="directory"),
    path("skills/", views.SkillListView.as_view(), name="skills"),
    path("new/", views.PersonCreateView.as_view(), name="create"),
    path("<int:pk>/activate/", views.PersonActivateView.as_view(), name="activate"),
    path("<int:pk>/deactivate/", views.PersonDeactivateView.as_view(), name="deactivate"),
    path("<int:pk>/delete/", views.PersonDeleteView.as_view(), name="delete"),
    path("<int:pk>/", views.PersonDetailView.as_view(), name="detail"),
]
