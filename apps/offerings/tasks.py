"""Periodic dispatch for recurring offerings."""

import logging

from celery import shared_task
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.offerings.models import OfferingStatus, ServiceOffering
from apps.offerings.scheduling import process_offering

logger = logging.getLogger(__name__)


@shared_task
def process_recurring_offerings():
    today = timezone.localdate()
    offerings = ServiceOffering.objects.filter(
        status=OfferingStatus.ACTIVE, on_schedule=True
    ).values_list("pk", flat=True)
    for offering_id in offerings.iterator():
        try:
            process_offering(offering_id, today)
        except ValidationError:
            logger.exception("Could not start recurring offering %s", offering_id)
