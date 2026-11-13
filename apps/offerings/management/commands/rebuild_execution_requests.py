"""Replace demo requests with one request per eligible execution."""

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.offerings.models import OfferingExecution
from apps.offerings.request_link import attach_missing_requests
from apps.requests.models import Request


class Command(BaseCommand):
    help = "Replace all requests with requests for unassociated or request-linked executions."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-delete-all-requests", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm_delete_all_requests"]:
            raise CommandError("Pass --confirm-delete-all-requests to confirm irreversible deletion.")
        request_type = ContentType.objects.get_for_model(Request)
        executions = OfferingExecution.objects.select_for_update().filter(subject_type=request_type)
        count = executions.update(subject_type=None, subject_id=None)
        deleted = Request.objects.count()
        Request.objects.all().delete()
        attach_missing_requests()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted} requests and rebuilt associations "
                f"({count} previously linked executions)."
            )
        )
