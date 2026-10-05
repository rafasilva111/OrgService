from django.contrib import admin

from apps.services.models import Service, ServiceActor, ServiceCapability, ServiceCapabilityAssignment, ServiceConsumption, ServiceType


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "dimension")
    search_fields = ("code", "name")
    list_filter = ("dimension",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "service_type", "status", "parent")
    list_filter = ("service_type", "status", "organization")
    search_fields = ("name", "organization__name")
    list_select_related = ("service_type", "organization", "parent")


@admin.register(ServiceActor)
class ServiceActorAdmin(admin.ModelAdmin):
    list_display = ("service", "role", "person", "actor_type")
    list_filter = ("actor_type", "role")
    search_fields = ("service__name", "person__full_name")


@admin.register(ServiceCapability)
class ServiceCapabilityAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(ServiceCapabilityAssignment)
class ServiceCapabilityAssignmentAdmin(admin.ModelAdmin):
    list_display = ("service", "capability", "required")
    list_filter = ("required",)


@admin.register(ServiceConsumption)
class ServiceConsumptionAdmin(admin.ModelAdmin):
    list_display = ("service", "consumed", "since")
    list_filter = ("service",)
    search_fields = ("service__name", "consumed__name")
