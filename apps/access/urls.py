"""Access: roles and permissions."""

from django.urls import path

from . import views

app_name = "access"

urlpatterns = [
    path("roles/", views.RoleListView.as_view(), name="roles"),
    path("permissions/", views.PermissionListView.as_view(), name="permissions"),
    path("roles/new/", views.RoleCreateView.as_view(), name="role_create"),
    path("roles/<int:pk>/activate/", views.RoleActivateView.as_view(), name="role_activate"),
    path("roles/<int:pk>/deactivate/", views.RoleDeactivateView.as_view(), name="role_deactivate"),
    path("roles/<int:pk>/delete/", views.RoleDeleteView.as_view(), name="role_delete"),
    path("roles/<int:pk>/", views.RoleDetailView.as_view(), name="role_detail"),
]
