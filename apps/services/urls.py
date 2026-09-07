"""Services: catalog list and per-service detail."""

from django.urls import path

from . import views

app_name = "services"

urlpatterns = [
    path("", views.ServiceListView.as_view(), name="index"),
    path("new/", views.ServiceCreateView.as_view(), name="create"),
    path("<int:pk>/activate/", views.ServiceActivateView.as_view(), name="activate"),
    path("<int:pk>/deactivate/", views.ServiceDeactivateView.as_view(), name="deactivate"),
    path("<int:pk>/delete/", views.ServiceDeleteView.as_view(), name="delete"),
    path("<int:pk>/", views.ServiceDetailView.as_view(), name="detail"),
]
