"""Incidents, problems and their linkage."""

import pytest
from django.db import IntegrityError, transaction

from apps.incidents.models import Incident, IncidentUpdate
from apps.problems.models import Problem, ProblemIncident


@pytest.fixture
def incident_graph(db, org_tree, make_service, make_person):
    service = make_service(org_tree["parish_a"])
    reporter = make_person(name="Reporter")
    incident = Incident.objects.create(
        service=service,
        title="Water break",
        priority="HIGH",
        urgency="HIGH",
        impact="HIGH",
        status="OPEN",
        reported_by=reporter,
    )
    update = IncidentUpdate.objects.create(
        incident=incident, author=reporter, message="Investigating", status="OPEN"
    )
    return {"service": service, "incident": incident, "update": update, "reporter": reporter}


@pytest.mark.django_db
def test_incident_and_updates(incident_graph):
    inc = incident_graph["incident"]
    assert list(inc.updates.all()) == [incident_graph["update"]]
    assert inc.status == "OPEN"
    inc.status = "RESOLVED"
    inc.save()
    assert Incident.objects.get(pk=inc.pk).status == "RESOLVED"
    assert inc.priority == "HIGH"


@pytest.mark.django_db
def test_problem_links_incidents(incident_graph):
    service = incident_graph["service"]
    second = Incident.objects.create(service=service, title="Water break 2", status="OPEN")
    problem = Problem.objects.create(service=service, title="Recurring water breaks", status="OPEN")
    ProblemIncident.objects.create(problem=problem, incident=incident_graph["incident"])
    ProblemIncident.objects.create(problem=problem, incident=second)
    assert problem.incident_links.count() == 2
    assert {pi.incident_id for pi in problem.incident_links.all()} == {
        incident_graph["incident"].pk,
        second.pk,
    }


@pytest.mark.django_db
def test_problem_incident_uniqueness(incident_graph):
    problem = Problem.objects.create(service=incident_graph["service"], title="P", status="OPEN")
    ProblemIncident.objects.create(problem=problem, incident=incident_graph["incident"])
    with pytest.raises(IntegrityError), transaction.atomic():
        ProblemIncident.objects.create(problem=problem, incident=incident_graph["incident"])
