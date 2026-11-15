"""Read-side queries for the citizen portal.

The portal does not use the staff RBAC scopes: the public catalogue is every
alive, active, non-retired service and its ACTIVE ``on_request`` offerings, and
a citizen only ever sees requests where they are the requester.
"""

from django.db.models import Count, Prefetch, Q

from apps.offerings.models import OfferingStatus, ServiceOffering, StepType
from apps.requests.models import Request, RequestStatus
from apps.services.models import Service, ServiceStatus

CLOSED_STATUSES = (
    RequestStatus.RESOLVED,
    RequestStatus.CLOSED,
    RequestStatus.CANCELLED,
    RequestStatus.FAILED,
)
# Steps a citizen may see by name on an offering page.
PUBLIC_STEP_TYPES = (StepType.TASK, StepType.APPROVAL)


def public_offerings():
    return (
        ServiceOffering.objects.filter(
            status=OfferingStatus.ACTIVE,
            on_request=True,
            service__in=public_services(),
        )
        .select_related("service", "service__organization", "offering_type")
        .order_by("name", "-version")
    )


def public_services():
    return (
        Service.objects.alive()
        .filter(active=True, organization__active=True, organization__deleted_at__isnull=True)
        .exclude(status=ServiceStatus.RETIRED)
        .select_related("organization")
    )


def catalogue_services(*, query="", organization=None, offering_type=None):
    """Services with at least one public offering, offerings prefetched."""
    offerings = public_offerings()
    if offering_type:
        offerings = offerings.filter(offering_type_id=offering_type)
    if query:
        offerings = offerings.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(service__name__icontains=query)
        )
    services = (
        public_services()
        .filter(pk__in=offerings.values("service_id"))
        .prefetch_related(Prefetch("offerings", queryset=offerings, to_attr="public_offerings"))
        .order_by("organization__path", "name")
    )
    if organization:
        services = services.filter(organization_id=organization)
    return services


def disrupted_services():
    return public_services().exclude(status=ServiceStatus.OPERATIONAL).order_by("name")


def public_steps(offering):
    return list(offering.steps.filter(step_type__in=PUBLIC_STEP_TYPES).order_by("order", "pk"))


def citizen_requests(user):
    """Requests the signed-in user asked for — nothing else."""
    if not user.is_authenticated or not user.is_active:
        return Request.objects.none()
    return Request.objects.filter(
        requester__user=user, requester__active=True, requester__deleted_at__isnull=True
    ).select_related("service", "service__organization", "offering", "offering__offering_type")


def request_counts(user):
    return citizen_requests(user).aggregate(
        open=Count("pk", filter=~Q(status__in=CLOSED_STATUSES)),
        closed=Count("pk", filter=Q(status__in=CLOSED_STATUSES)),
    )
