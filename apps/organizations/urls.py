"""Organizations: hierarchy, tree and membership surfaces."""

from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path("", views.OrganizationListView.as_view(), name="index"),
    path("tree/", views.OrganizationTreeView.as_view(), name="tree"),
    path("memberships/", views.MembershipListView.as_view(), name="memberships"),
    path("new/", views.OrganizationCreateView.as_view(), name="create"),
    path("<int:pk>/activate/", views.OrganizationActivateView.as_view(), name="activate"),
    path("<int:pk>/deactivate/", views.OrganizationDeactivateView.as_view(), name="deactivate"),
    path("<int:pk>/delete/", views.OrganizationDeleteView.as_view(), name="delete"),
    path("<int:pk>/", views.OrganizationDetailView.as_view(), name="detail"),
]
