"""Changes: change management and approvals."""

import pytest

from apps.changes.models import Change, ChangeApproval


@pytest.mark.django_db
def test_change_and_approvals(org_tree, make_service, make_person):
    service = make_service(org_tree["parish_a"], name="Network")
    author = make_person()
    change = Change.objects.create(
        service=service, title="Upgrade router", requested_by=author, status="PROPOSED"
    )
    approver = make_person(name="Approver")
    ChangeApproval.objects.create(change=change, approver=approver, decision="APPROVED")
    assert change.service == service
    assert change.requested_by == author
    approval = change.approvals.get()
    assert approval.decision == "APPROVED"
    assert approval.approver == approver
    assert str(change) == f"#{change.pk} Upgrade router"


@pytest.mark.django_db
def test_approval_unique_per_approver(org_tree, make_service, make_person):
    import pytest as _pytest
    from django.db import IntegrityError, transaction

    change = Change.objects.create(service=make_service(org_tree["parish_a"]), title="x")
    approver = make_person()
    ChangeApproval.objects.create(change=change, approver=approver)
    with _pytest.raises(IntegrityError), transaction.atomic():
        ChangeApproval.objects.create(change=change, approver=approver)


@pytest.mark.django_db
def test_change_status_flow(org_tree, make_service):
    change = Change.objects.create(
        service=make_service(org_tree["parish_a"]),
        title="Add capacity",
        status="APPROVED",
    )
    assert Change.objects.get(pk=change.pk).status == "APPROVED"
