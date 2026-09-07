"""Seed a realistic default dataset for local development / demos.

Usage: ``python manage.py seed_default_data [--reset]``

Idempotent: identifiers are stable (codes/emails), so re-running updates
rather than duplicates. Transactional samples (requests, incidents, …) are
only created when the respective table is empty to keep re-runs cheap.

The demo org tree mirrors a real Portuguese geography:

    Portugal (COUNTRY)
    └── Lisboa (DISTRICT)
        └── Torres Vedras (MUNICIPALITY)
            ├── São Pedro e Santiago (PARISH)
            ├── Matacães (PARISH)
            ├── A dos Cunhados e Maceira (PARISH)
            └── Ramalhal (PARISH)

Every seeded Service is bound to a Service Manager actor; ``--reset`` wipes the
existing domain data first (demo re-seed).

Also bootstraps a superuser (env-driven) and demo accounts, and seeds the RBAC
catalogue by delegating to ``seed_rbac``.
"""

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.access.models import Role
from apps.audit.models import AuditLog
from apps.changes.models import Change, ChangeApproval
from apps.events.models import Event
from apps.incidents.models import Impact, Incident, IncidentStatus, IncidentUpdate, Urgency
from apps.metrics.models import KPI, SLA, AggregationType, Metric, MetricValue, ScopeType
from apps.organizations.models import Organization, OrganizationMembership
from apps.people.models import Person, PersonType, Skill
from apps.problems.models import Problem
from apps.requests.models import Priority, Request, RequestComment, RequestStatus, RequestType
from apps.resources.models import Equipment, Resource, ResourceAssignment, ResourceType
from apps.services.models import ActorType, Service, ServiceActor, ServiceStatus
from apps.workflows.models import (
    ExecutionStatus,
    StepType,
    Workflow,
    WorkflowExecution,
    WorkflowStatus,
    WorkflowStep,
    WorkflowTask,
    WorkflowTransition,
    WorkflowTrigger,
)

NOW = timezone.now()

# Domain tables reset by ``--reset`` (everything except RBAC + auth catalogue).
_RESET_PREFIXES = (
    "organizations_",
    "services_",
    "workflows_",
    "requests_",
    "incidents_",
    "problems_",
    "changes_",
    "resources_",
    "metrics_",
    "events_",
    "audit_",
    "access_serviceaccess",
    "people_",
)


def _code(prefix, slug):
    return f"{prefix}_{slug}"


class Command(BaseCommand):
    help = "Seed a default dataset (orgs, services, people, RBAC, sample operational records)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the existing domain data before seeding (demo re-seed).",
        )

    def handle(self, *args, **options):
        if options.get("reset"):
            self._reset_domain_data()
        call_command("seed_rbac", verbosity=0)
        self._seed_superuser()
        orgs = self._seed_org_tree()
        services = self._seed_services(orgs)
        people = self._seed_people(orgs, services)
        self._seed_request_types(services)
        self._seed_skills()
        self._seed_workflows(services, people)
        self._seed_requests(services, people)
        self._seed_incidents(services, people)
        self._seed_problems(services, people)
        self._seed_changes(services, people)
        self._seed_resources(orgs, services, people)
        self._seed_metrics(services)
        self._seed_wf_executions(people)
        self._seed_events(services, people)
        self._seed_audit(services, people)

        accounts = get_user_model().objects.filter(is_active=True).order_by("username")
        self.stdout.write(self.style.SUCCESS("Default dataset ready."))
        self.stdout.write("- Logins: " + ", ".join(a.username for a in accounts))
        self.stdout.write(
            "- Superuser password: " + os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin123")
        )

    # ------------------------------------------------------------------- reset
    def _reset_domain_data(self):
        tables = [
            t.name
            for t in connection.introspection.get_table_list(connection.cursor())
            if t.name.startswith(_RESET_PREFIXES)
        ]
        if not tables:
            self.stdout.write(self.style.WARNING("No domain tables to reset."))
            return
        names = ", ".join(f'"{t}"' for t in tables)
        with connection.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
        self.stdout.write(self.style.WARNING(f"Reset {len(tables)} domain tables."))
        with connection.cursor() as cur:
            cur.execute("DELETE FROM auth_user WHERE is_superuser = false AND is_staff = false")
            self.stdout.write(self.style.WARNING(f"Purged {cur.rowcount} stale demo users."))

    # ------------------------------------------------------------------ admin
    def _seed_superuser(self):
        User = get_user_model()
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.local")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin123")
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_staff": True, "is_superuser": True},
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.WARNING(f"Created superuser '{username}'."))
        person, _ = Person.objects.get_or_create(
            email=email,
            defaults={
                "full_name": "System Administrator",
                "person_type": PersonType.EMPLOYEE,
                "user": user,
            },
        )
        if person.user_id != user.id:
            person.user = user
            person.save()

    # ---------------------------------------------------------------- org tree
    def _seed_org_tree(self):
        catalog = [
            (
                "portugal",
                "Portugal",
                "COUNTRY",
                None,
                "Portuguese Republic — root of the demo platform.",
            ),
            ("lisboa", "Lisboa", "DISTRICT", "portugal", "Historical district of Lisboa."),
            (
                "torres-vedras",
                "Torres Vedras",
                "MUNICIPALITY",
                "lisboa",
                "Municipality in the Lisboa district.",
            ),
            (
                "sao-pedro-santiago",
                "São Pedro e Santiago",
                "PARISH",
                "torres-vedras",
                "Urban parish of Torres Vedras.",
            ),
            (
                "matacaes",
                "Matacães",
                "PARISH",
                "torres-vedras",
                "Parish west of Torres Vedras town.",
            ),
            (
                "a-dos-cunhados",
                "A dos Cunhados e Maceira",
                "PARISH",
                "torres-vedras",
                "Coastal parish of Torres Vedras.",
            ),
            (
                "ramalhal",
                "Ramalhal",
                "PARISH",
                "torres-vedras",
                "Parish inland from Torres Vedras.",
            ),
        ]
        orgs = {}
        for code, name, otype, parent_code, desc in catalog:
            parent = orgs[parent_code] if parent_code else None
            org, created = Organization.objects.get_or_create(
                code=code,
                parent=parent,
                defaults={
                    "name": name,
                    "organization_type": otype,
                    "description": desc,
                },
            )
            if not created and (
                org.name != name
                or org.organization_type != otype
                or org.description != desc
                or org.parent_id != (parent.pk if parent else None)
            ):
                org.name = name
                org.organization_type = otype
                org.description = desc
                org.parent = parent
                org.save()
            orgs[code] = org
        return orgs

    # ----------------------------------------------------------------- services
    def _seed_services(self, orgs):
        catalog = [
            ("Municipal Administration", "torres-vedras", ServiceStatus.OPERATIONAL),
            ("Water Supply", "torres-vedras", ServiceStatus.DEGRADED),
            ("Sanitation Network", "torres-vedras", ServiceStatus.OPERATIONAL),
            ("Waste Management", "torres-vedras", ServiceStatus.OPERATIONAL),
            ("Road Maintenance", "torres-vedras", ServiceStatus.OPERATIONAL),
            ("Public Lighting", "torres-vedras", ServiceStatus.OPERATIONAL),
            ("Public Health", "sao-pedro-santiago", ServiceStatus.OPERATIONAL),
            ("Urban Cleaning", "sao-pedro-santiago", ServiceStatus.OPERATIONAL),
            ("Green Spaces & Parks", "matacaes", ServiceStatus.OPERATIONAL),
        ]
        services = {}
        for name, org_key, status in catalog:
            org = orgs[org_key]
            service, _ = Service.objects.get_or_create(
                organization=org,
                name=name,
                defaults={
                    "description": f"Service of {org.name}: {name}.",
                    "status": status,
                },
            )
            if service.status != status:
                service.status = status
                service.save()
            services[name] = service
        return services

    # ------------------------------------------------------------------- people
    def _seed_people(self, orgs, services):
        User = get_user_model()
        demo = [
            ("r.martins", "Rita Martins", "rita.martins@torres-vedras.local", "EXECUTIVE"),
            (
                "j.fernandes",
                "José Fernandes",
                "jose.fernandes@torres-vedras.local",
                "SERVICE_MANAGER",
            ),
            ("a.duarte", "Ana Duarte", "ana.duarte@torres-vedras.local", "SERVICE_MANAGER"),
            ("t.pereira", "Tiago Pereira", "tiago.pereira@torres-vedras.local", "SERVICE_MANAGER"),
            ("s.almeida", "Sofia Almeida", "sofia.almeida@torres-vedras.local", "SERVICE_MANAGER"),
            ("m.silva", "Mariana Silva", "mariana.silva@torres-vedras.local", "SERVICE_MANAGER"),
            ("r.costa", "Rui Costa", "rui.costa@torres-vedras.local", "SERVICE_MANAGER"),
            ("n.barreto", "Nuno Barreto", "nuno.barreto@torres-vedras.local", "SERVICE_MANAGER"),
            ("p.lopes", "Paula Lopes", "paula.lopes@torres-vedras.local", "SERVICE_MANAGER"),
            (
                "l.rodrigues",
                "Luís Rodrigues",
                "luis.rodrigues@torres-vedras.local",
                "OPERATIONS_MANAGER",
            ),
            ("p.nunes", "Pedro Nunes", "pedro.nunes@torres-vedras.local", "OPERATOR"),
            ("b.lopes", "Beatriz Lopes", "beatriz.lopes@torres-vedras.local", "INCIDENT_MANAGER"),
            ("h.ferreira", "Hugo Ferreira", "hugo.ferreira@torres-vedras.local", "PROBLEM_MANAGER"),
            ("i.cardoso", "Inês Cardoso", "ines.cardoso@torres-vedras.local", "ANALYST"),
            ("m.mendes", "Marta Mendes", "marta.mendes@torres-vedras.local", "REQUEST_MANAGER"),
        ]
        people = {}
        for username, full_name, email, role_code in demo:
            user, created = User.objects.get_or_create(username=username, defaults={"email": email})
            if created:
                user.set_password(f"{username}123")
                user.save()
                self.stdout.write(
                    self.style.WARNING(f"Created demo user '{username}' / '{username}123'.")
                )
            person, _ = Person.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": full_name,
                    "person_type": PersonType.EMPLOYEE,
                    "user": user,
                },
            )
            if person.user_id != user.id:
                person.user = user
                person.save()
            people[username] = person

        tv = orgs["torres-vedras"]
        role_exec = Role.objects.get(code="EXECUTIVE")
        role_sm = Role.objects.get(code="SERVICE_MANAGER")
        role_om = Role.objects.get(code="OPERATIONS_MANAGER")
        role_op = Role.objects.get(code="OPERATOR")
        role_rm = Role.objects.get(code="REQUEST_MANAGER")
        role_im = Role.objects.get(code="INCIDENT_MANAGER")
        role_pm = Role.objects.get(code="PROBLEM_MANAGER")
        role_an = Role.objects.get(code="ANALYST")

        memberships = [
            ("r.martins", tv, role_exec),
            ("j.fernandes", tv, role_sm),
            ("a.duarte", tv, role_sm),
            ("t.pereira", tv, role_sm),
            ("s.almeida", tv, role_sm),
            ("m.silva", tv, role_sm),
            ("r.costa", tv, role_sm),
            ("n.barreto", tv, role_sm),
            ("p.lopes", tv, role_sm),
            ("l.rodrigues", tv, role_om),
            ("m.mendes", tv, role_rm),
            ("b.lopes", tv, role_im),
            ("h.ferreira", tv, role_pm),
            ("i.cardoso", tv, role_an),
            ("p.nunes", orgs["sao-pedro-santiago"], role_op),
        ]
        for username, org, role in memberships:
            OrganizationMembership.objects.get_or_create(
                person=people[username],
                organization=org,
                role=role,
                defaults={"active": True},
            )

        # Give the platform admin (superuser) every existing role at Torres
        # Vedras, so the header service/role selector can swap between all
        # contexts (a person may hold several roles in the same org).
        admin_person = Person.objects.filter(email=os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.local")).first()
        if admin_person is not None:
            for role in Role.objects.all():
                OrganizationMembership.objects.get_or_create(
                    person=admin_person,
                    organization=tv,
                    role=role,
                    defaults={"active": True},
                )

        # Every service needs a Service Manager actor <-> person.
        managers = {
            "Municipal Administration": "r.martins",
            "Water Supply": "j.fernandes",
            "Sanitation Network": "n.barreto",
            "Waste Management": "a.duarte",
            "Road Maintenance": "t.pereira",
            "Public Lighting": "s.almeida",
            "Public Health": "m.silva",
            "Urban Cleaning": "p.lopes",
            "Green Spaces & Parks": "r.costa",
        }
        for svc_name, username in managers.items():
            ServiceActor.objects.get_or_create(
                service=services[svc_name],
                role=role_sm,
                person=people[username],
                defaults={"actor_type": ActorType.INTERNAL, "active": True},
            )

        extra_actors = [
            ("Water Supply", "l.rodrigues", role_om),
            ("Waste Management", "p.nunes", role_op),
            ("Road Maintenance", "p.nunes", role_op),
        ]
        for svc_name, username, role in extra_actors:
            ServiceActor.objects.get_or_create(
                service=services[svc_name],
                role=role,
                person=people[username],
                defaults={"actor_type": ActorType.INTERNAL, "active": True},
            )

        for svc_name, service in services.items():
            if not service.actors.filter(role=role_sm, active=True).exists():
                self.stderr.write(self.style.ERROR(f"Service '{svc_name}' has NO Service Manager!"))
        return people

    def _service_by_name(self, name):
        return Service.objects.get(name=name)

    # ----------------------------------------------------------- request types
    def _seed_request_types(self, services):
        catalog = [
            ("NEW_EQUIPMENT", "New Equipment", None),
            ("MAINTENANCE", "Maintenance Request", services.get("Water Supply")),
            ("COMPLAINT", "Public Complaint", None),
            ("GENERAL", "General Request", None),
        ]
        for code, name, service in catalog:
            RequestType.objects.get_or_create(
                code=code,
                defaults={"name": name, "service": service},
            )

    # ------------------------------------------------------------------- skills
    def _seed_skills(self):
        for code, name in [
            ("vehicle_operator", "Vehicle Operator"),
            ("electrician", "Electrician"),
            ("plumber", "Plumber"),
            ("hse", "HSE Officer"),
        ]:
            Skill.objects.get_or_create(code=code, defaults={"name": name})

    # ---------------------------------------------------------------- workflows
    def _seed_workflows(self, services, people):
        role_op = Role.objects.get(code="OPERATOR")
        role_sm = Role.objects.get(code="SERVICE_MANAGER")

        def build(name, service, description, steps):
            wf, _ = Workflow.objects.get_or_create(
                service=service,
                name=name,
                defaults={
                    "description": description,
                    "version": 1,
                    "status": WorkflowStatus.ACTIVE,
                    "trigger": WorkflowTrigger.REQUEST,
                    "created_by": people.get("a.duarte"),
                },
            )
            created = {}
            for code, sname, step_type, order, role, inputs, sla in steps:
                step, _ = WorkflowStep.objects.get_or_create(
                    workflow=wf,
                    code=code,
                    defaults={
                        "name": sname,
                        "step_type": step_type,
                        "order": order,
                        "responsible_role": role,
                        "required_inputs": inputs,
                        "sla_minutes": sla,
                    },
                )
                created[code] = step
            transitions = [
                ("assign", "start", 10),
                ("collect", "assign", 10),
                ("verify", "collect", 10),
                ("end", "verify", 10),
            ]
            for to_code, from_code, order in transitions:
                if to_code not in created or from_code not in created:
                    continue
                WorkflowTransition.objects.get_or_create(
                    workflow=wf,
                    from_step=created[from_code],
                    order=order,
                    defaults={"to_step": created[to_code]},
                )
            return wf

        build(
            "Waste Collection Request",
            services["Waste Management"],
            "End-to-end request handling for waste collection.",
            [
                ("start", "Start", StepType.START, 10, None, [], None),
                ("assign", "Assign Operator", StepType.TASK, 20, role_op, [], None),
                ("collect", "Collect Waste", StepType.TASK, 30, role_op, ["vehicle"], None),
                ("verify", "Verify Completion", StepType.APPROVAL, 40, role_sm, [], 240),
                ("end", "End", StepType.END, 50, None, [], None),
            ],
        )
        build(
            "Water Maintenance Request",
            services["Water Supply"],
            "Request handling for water supply repairs and connections.",
            [
                ("start", "Start", StepType.START, 10, None, [], None),
                ("assign", "Assign Crew", StepType.TASK, 20, role_op, [], None),
                ("repair", "Carry Out Repair", StepType.TASK, 30, role_op, [], None),
                ("verify", "Verify Fix", StepType.APPROVAL, 40, role_sm, [], 240),
                ("end", "End", StepType.END, 50, None, [], None),
            ],
        )

    # ---------------------------------------------------------------- requests
    def _seed_requests(self, services, people):
        wf_waste = Workflow.objects.filter(name="Waste Collection Request").first()
        wf_water = Workflow.objects.filter(name="Water Maintenance Request").first()
        # (service, type_code, title, desc, status, priority, assigned_key, wf)
        samples = [
            # --- Waste Management
            (
                services["Waste Management"],
                "NEW_EQUIPMENT",
                "New skip bins for the market",
                "Requesting two additional 8m³ skip bins for the municipal market.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "p.nunes",
                wf_waste,
            ),
            (
                services["Waste Management"],
                "COMPLAINT",
                "Missed collection on Rua de São Miguel",
                "Residents report no collection for the past two weeks on Rua de São Miguel.",
                RequestStatus.NEW,
                Priority.HIGH,
                None,
                wf_waste,
            ),
            (
                services["Waste Management"],
                "NEW_EQUIPMENT",
                "Broken skip bin lid",
                "Skip bin at the market square has a damaged lid that cannot be closed securely.",
                RequestStatus.NEW,
                Priority.LOW,
                None,
                wf_waste,
            ),
            (
                services["Waste Management"],
                "COMPLAINT",
                "Tyres dumped near the cemetery",
                "A pile of old tyres was abandoned on the access road behind the cemetery.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "p.nunes",
                wf_waste,
            ),
            # --- Water Supply
            (
                services["Water Supply"],
                "GENERAL",
                "Borehole inspection on Rua do Forte",
                "Schedule a preventative inspection of the Matacães borehole.",
                RequestStatus.WAITING,
                Priority.LOW,
                None,
                wf_water,
            ),
            (
                services["Water Supply"],
                "COMPLAINT",
                "Leak in front of the school",
                "Water leaking from the main in front of the primary school; pavers lifting.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "j.fernandes",
                wf_water,
            ),
            (
                services["Water Supply"],
                "NEW_EQUIPMENT",
                "New connection for the bakery",
                "Bakery on Rua Augusta requesting a new water connection.",
                RequestStatus.WAITING,
                Priority.MEDIUM,
                None,
                wf_water,
            ),
            (
                services["Water Supply"],
                "MAINTENANCE",
                "Hydrant check on Praça do Município",
                "Annual inspection of the public hydrant on the municipal square.",
                RequestStatus.NEW,
                Priority.LOW,
                None,
                wf_water,
            ),
            # --- Road Maintenance
            (
                services["Road Maintenance"],
                "COMPLAINT",
                "Sinkhole near the roundabout",
                "Sinkhole forming on the approach to the EN8 roundabout; kernel hazard.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "p.nunes",
                None,
            ),
            (
                services["Road Maintenance"],
                "GENERAL",
                "Repave Rua dos Ciprestes",
                "Surface crumbling on Rua dos Ciprestes; full resurfacing requested.",
                RequestStatus.WAITING,
                Priority.MEDIUM,
                None,
                None,
            ),
            (
                services["Road Maintenance"],
                "COMPLAINT",
                "Damaged pedestrian crossing",
                "Faded striping on the pedestrian crossing near the station.",
                RequestStatus.NEW,
                Priority.MEDIUM,
                None,
                None,
            ),
            # --- Public Lighting
            (
                services["Public Lighting"],
                "COMPLAINT",
                "Streetlight outage on Av. 25 de Abril",
                "Three streetlights out along Avenida 25 de Abril near the stadium.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "t.pereira",
                None,
            ),
            (
                services["Public Lighting"],
                "NEW_EQUIPMENT",
                "New lamppost for the park",
                "Additional lamppost requested for the children's playground.",
                RequestStatus.WAITING,
                Priority.LOW,
                None,
                None,
            ),
            # --- Sanitation Network
            (
                services["Sanitation Network"],
                "COMPLAINT",
                "Blocked sewer on Rua do Moinho",
                "Sewer backing up at Rua do Moinho; odours in neighbouring houses.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "n.barreto",
                None,
            ),
            (
                services["Sanitation Network"],
                "MAINTENANCE",
                "Replacement of manhole cover",
                "Cracked manhole cover in the middle of Rua da Fonte.",
                RequestStatus.NEW,
                Priority.MEDIUM,
                None,
                None,
            ),
            # --- Green Spaces & Parks
            (
                services["Green Spaces & Parks"],
                "GENERAL",
                "Tree pruning in Praça do Município",
                "Prune the cork oaks in Praça do Município before the summer.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "r.costa",
                None,
            ),
            (
                services["Green Spaces & Parks"],
                "COMPLAINT",
                "Playground swing repair",
                "One swing broken in the Matacães playground.",
                RequestStatus.NEW,
                Priority.LOW,
                None,
                None,
            ),
            # --- Public Health
            (
                services["Public Health"],
                "GENERAL",
                "Rat control campaign",
                "Planning a community rat-control and hygiene awareness campaign.",
                RequestStatus.RESOLVED,
                Priority.MEDIUM,
                "p.nunes",
                None,
            ),
            (
                services["Public Health"],
                "GENERAL",
                "Mosquito breeding hotspots",
                "Identified standing-water hotspots around the market needing treatment.",
                RequestStatus.NEW,
                Priority.MEDIUM,
                None,
                None,
            ),
            (
                services["Public Health"],
                "GENERAL",
                "Food safety inspection",
                "Routine inspection of the summer festival food stalls.",
                RequestStatus.WAITING,
                Priority.MEDIUM,
                "i.cardoso",
                None,
            ),
            # --- Urban Cleaning
            (
                services["Urban Cleaning"],
                "COMPLAINT",
                "Illegal dumping on the Matacães road",
                "Furniture and construction debris dumped on the roadside.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "p.nunes",
                None,
            ),
            (
                services["Urban Cleaning"],
                "COMPLAINT",
                "Graffiti removal at the station",
                "Graffiti covering the pedestrian underpass at the station.",
                RequestStatus.NEW,
                Priority.LOW,
                None,
                None,
            ),
            # --- Municipal Administration
            (
                services["Municipal Administration"],
                "GENERAL",
                "Archives records request",
                "Citizen requesting urbanism records for a property in the old town.",
                RequestStatus.RESOLVED,
                Priority.LOW,
                None,
                None,
            ),
            (
                services["Municipal Administration"],
                "GENERAL",
                "Licence renewal for street vendor",
                "Renewal of the market license for a vegetable vendor.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "m.mendes",
                None,
            ),
        ]
        for service, rtype_code, title, desc, status, priority, assigned_key, workflow in samples:
            req, _ = Request.objects.get_or_create(
                service=service,
                title=title,
                defaults={
                    "request_type": RequestType.objects.get(code=rtype_code),
                    "description": desc,
                    "requester": people.get("i.cardoso"),
                    "created_by": people.get("m.mendes"),
                    "status": status,
                    "priority": priority,
                    "assigned_to": people.get(assigned_key) if assigned_key else None,
                    "workflow": workflow,
                },
            )
            if not RequestComment.objects.filter(request=req).exists():
                RequestComment.objects.create(
                    request=req,
                    author=people.get("m.mendes"),
                    body="Logged during demo seeding — pending review.",
                )

    # ---------------------------------------------------------------- incidents
    def _seed_incidents(self, services, people):
        samples = [
            # (service, title, desc, status, prio, impact, urgency, assigned_key)
            (
                services["Water Supply"],
                "Burst main on Rua do Espírito Santo",
                "Water main rupture causing supply loss and flooding on Rua do Espírito Santo.",
                IncidentStatus.INVESTIGATING,
                "HIGH",
                Impact.HIGH.value,
                Urgency.HIGH.value,
                "b.lopes",
            ),
            (
                services["Water Supply"],
                "Supply pressure drop in Matacães",
                "Residents report low pressure across Matacães since the morning.",
                IncidentStatus.OPEN,
                "MEDIUM",
                Impact.MEDIUM.value,
                Urgency.HIGH.value,
                None,
            ),
            (
                services["Road Maintenance"],
                "Pothole cluster on Estrada Nacional 8",
                "Multiple large potholes near the Matacães junction; risk to traffic.",
                IncidentStatus.OPEN,
                "MEDIUM",
                Impact.MEDIUM.value,
                Urgency.MEDIUM.value,
                None,
            ),
            (
                services["Road Maintenance"],
                "Collapsed verge near Ramalhal",
                "Verge collapsed after heavy rain; half lane closed.",
                IncidentStatus.INVESTIGATING,
                "HIGH",
                Impact.HIGH.value,
                Urgency.HIGH.value,
                "p.nunes",
            ),
            (
                services["Waste Management"],
                "Collection vehicle breakdown",
                "Skip truck TV-103 broke down during the morning round.",
                IncidentStatus.RESOLVED,
                "MEDIUM",
                Impact.MEDIUM.value,
                Urgency.MEDIUM.value,
                "p.nunes",
            ),
            (
                services["Public Lighting"],
                "Substation fault leaves quarter in darkness",
                "Feeder fault blacked out street lighting in the eastern quarter.",
                IncidentStatus.OPEN,
                "HIGH",
                Impact.HIGH.value,
                Urgency.HIGH.value,
                "s.almeida",
            ),
            (
                services["Sanitation Network"],
                "Overflow at the pumping station",
                "Raw overflow at the Matacães pumping station; possible odours and hazard.",
                IncidentStatus.INVESTIGATING,
                "HIGH",
                Impact.HIGH.value,
                Urgency.HIGH.value,
                "n.barreto",
            ),
            (
                services["Public Health"],
                "Suspected contamination at the fountain",
                "Test results flagged possible contamination at the public fountain.",
                IncidentStatus.OPEN,
                "MEDIUM",
                Impact.MEDIUM.value,
                Urgency.HIGH.value,
                "b.lopes",
            ),
        ]
        for service, title, desc, status, prio, impact, urgency, assigned in samples:
            incident, _ = Incident.objects.get_or_create(
                service=service,
                title=title,
                defaults={
                    "description": desc,
                    "reported_by": people.get("i.cardoso"),
                    "assigned_to": people.get(assigned) if assigned else None,
                    "priority": prio,
                    "impact": impact,
                    "urgency": urgency,
                    "status": status,
                    "started_at": NOW - timedelta(days=1),
                    "resolved_at": NOW + timedelta(days=2)
                    if status == IncidentStatus.RESOLVED
                    else None,
                },
            )
            if not IncidentUpdate.objects.filter(incident=incident).exists():
                IncidentUpdate.objects.create(
                    incident=incident,
                    author=people.get("b.lopes"),
                    message="Initial triage completed during demo seeding.",
                    status=status,
                )

    # ---------------------------------------------------------------- problems
    def _seed_problems(self, services, people):
        first_incident = Incident.objects.first()
        problems = [
            (
                services["Water Supply"],
                "Recurring water main failures",
                "Multiple related bursts over the past quarter on the same trunk line.",
                "OPEN",
                Priority.HIGH,
                "h.ferreira",
                "Corroded aging cast-iron section.",
            ),
            (
                services["Public Lighting"],
                "Failing lighting control gear",
                "Repeated unit failures across the eastern substation switchgear.",
                "OPEN",
                Priority.MEDIUM,
                "h.ferreira",
                "Aging electro-mechanical relays.",
            ),
            (
                services["Sanitation Network"],
                "Sewer blockages in the old town",
                "Recurring blockages in the narrow pipes of the historic centre.",
                "INVESTIGATION",
                Priority.MEDIUM,
                "h.ferreira",
                "",
            ),
        ]
        for service, title, desc, status, priority, assigned, cause in problems:
            problem, _ = Problem.objects.get_or_create(
                service=service,
                title=title,
                defaults={
                    "description": desc,
                    "status": status,
                    "priority": priority,
                    "assigned_to": people.get(assigned),
                    "root_cause": cause,
                },
            )
            if (
                first_incident
                and not problem.incident_links.filter(incident=first_incident).exists()
            ):
                from apps.problems.models import ProblemIncident

                ProblemIncident.objects.get_or_create(problem=problem, incident=first_incident)

    # ------------------------------------------------------------------ changes
    def _seed_changes(self, services, people):
        changes = [
            (
                services["Water Supply"],
                "Replace aging trunk main section",
                "Replace 400m of corroded cast-iron trunk main on Rua do Espírito Santo.",
                "Prevent recurring bursts and supply outages.",
                "HIGH",
                "PROPOSED",
                "h.ferreira",
                7,
                9,
            ),
            (
                services["Waste Management"],
                "Smart bins pilot rollout",
                "Deploy 40 sensor-equipped bins across the market zone.",
                "Improve collection efficiency and offload forecasting.",
                "MEDIUM",
                "SCHEDULED",
                "h.ferreira",
                14,
                21,
            ),
            (
                services["Road Maintenance"],
                "Traffic light controller replacement",
                "Replace the EN8 junction controller with a new unit.",
                "Obsolete hardware, repeated signal faults.",
                "LOW",
                "PROPOSED",
                "h.ferreira",
                21,
                23,
            ),
            (
                services["Water Supply"],
                "Reservoir monitoring telemetry",
                "Install level and pressure telemetry at the water tower.",
                "Enable remote monitoring and leak alerts.",
                "MEDIUM",
                "SCHEDULED",
                "h.ferreira",
                10,
                12,
            ),
        ]
        for service, title, desc, reason, risk, status, requested_by, d_start, d_end in changes:
            change, _ = Change.objects.get_or_create(
                service=service,
                title=title,
                defaults={
                    "organization": service.organization,
                    "description": desc,
                    "reason": reason,
                    "risk": risk,
                    "status": status,
                    "requested_by": people.get(requested_by),
                    "planned_start": NOW + timedelta(days=d_start),
                    "planned_end": NOW + timedelta(days=d_end),
                },
            )
            ChangeApproval.objects.get_or_create(
                change=change,
                approver=people.get("r.martins"),
                defaults={"role": Role.objects.get(code="EXECUTIVE")},
            )

    # ---------------------------------------------------------------- resources
    def _seed_resources(self, orgs, services, people):
        if Resource.objects.exists():
            return
        municipality = orgs["torres-vedras"]
        resources = [
            (
                ResourceType.VEHICLE,
                "Skip truck TV-103",
                "_vehicle_tv103",
                municipality,
                services["Waste Management"],
            ),
            (
                ResourceType.EQUIPMENT,
                "Bulk water tanker",
                "_tanker_tv001",
                municipality,
                services["Water Supply"],
            ),
            (
                ResourceType.EQUIPMENT,
                "Road compactor",
                "_compactor_tv001",
                municipality,
                services["Road Maintenance"],
            ),
        ]
        for rtype, name, code, org, service in resources:
            widget = Resource if rtype == ResourceType.VEHICLE else Equipment
            resource, _ = widget.objects.get_or_create(
                code=code,
                defaults={
                    "resource_type": rtype,
                    "name": name,
                    "organization": org,
                    "service": service,
                    "active": True,
                },
            )
            if people.get("p.nunes"):
                from django.contrib.contenttypes.models import ContentType

                op = people["p.nunes"]
                ResourceAssignment.objects.get_or_create(
                    resource=resource,
                    target_type=ContentType.objects.get_for_model(op),
                    target_id=op.id,
                    defaults={"label": "Field operator", "started_at": NOW},
                )

    # ------------------------------------------------------------------ metrics
    def _seed_metrics(self, services):
        mt_waste = services["Waste Management"]
        catalog = [
            ("requests_created", "Requests Created", "count", AggregationType.COUNT, mt_waste),
            ("requests_resolved", "Requests Resolved", "count", AggregationType.COUNT, mt_waste),
            (
                "avg_resolution_hours",
                "Avg Resolution (hours)",
                "hours",
                AggregationType.AVG,
                mt_waste,
            ),
            ("platform_open_incidents", "Open Incidents", "count", AggregationType.COUNT, None),
        ]
        metrics = {}
        for code, name, unit, agg, service in catalog:
            metric, _ = Metric.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "unit": unit,
                    "aggregation": agg,
                    "service": service,
                    "description": f"Demo metric: {name}.",
                },
            )
            metrics[code] = metric

        # Six months of monthly values for the waste-service health story.
        created_values = [
            (metrics["requests_created"], 118),
            (metrics["requests_created"], 124),
            (metrics["requests_created"], 131),
            (metrics["requests_created"], 140),
            (metrics["requests_created"], 146),
            (metrics["requests_created"], 152),
            (metrics["requests_resolved"], 96),
            (metrics["requests_resolved"], 112),
            (metrics["requests_resolved"], 121),
            (metrics["requests_resolved"], 130),
            (metrics["requests_resolved"], 139),
            (metrics["requests_resolved"], 149),
        ]
        for i, (metric, value) in enumerate(created_values):
            month_start = NOW.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(
                days=30 * (5 - i)
            )
            month_end = (month_start + timedelta(days=31)).replace(day=1) - timedelta(
                microseconds=1
            )
            MetricValue.objects.get_or_create(
                metric=metric,
                period_start=month_start,
                defaults={
                    "value": value,
                    "period_end": month_end,
                },
            )

        kpi, _ = KPI.objects.get_or_create(
            code="waste_service_health",
            defaults={
                "name": "Waste Service Health",
                "description": "Blended health score for waste management.",
                "service": mt_waste,
                "target_value": 90,
                "tolerance": 5,
            },
        )
        for code in ("requests_created", "requests_resolved"):
            from apps.metrics.models import KpiMetric

            KpiMetric.objects.get_or_create(kpi=kpi, metric=metrics[code], defaults={"weight": 1})

        if not SLA.objects.exists():
            SLA.objects.get_or_create(
                service=mt_waste,
                name="Response target",
                scope=ScopeType.RESPONSE,
                defaults={"priority": "HIGH", "target_minutes": 240},
            )
            SLA.objects.get_or_create(
                service=mt_waste,
                name="Resolution target",
                scope=ScopeType.RESOLUTION,
                defaults={"priority": "HIGH", "target_minutes": 1440},
            )

    # -------------------------------------------------------------- executions
    def _seed_wf_executions(self, people):
        def add_execution(wf, current_code, status, started, completed=None):
            if not wf:
                return
            steps = {s.code: s for s in wf.steps.all()}
            execn, _ = WorkflowExecution.objects.get_or_create(
                workflow=wf,
                started_at=started,
                defaults={
                    "status": status,
                    "current_step": steps.get(current_code),
                    "completed_at": completed,
                    "created_by": people.get("a.duarte"),
                },
            )
            return execn, steps

        wf_waste = Workflow.objects.filter(name="Waste Collection Request").first()
        exec1, steps = add_execution(
            wf_waste,
            "collect",
            ExecutionStatus.RUNNING,
            NOW - timedelta(hours=3),
        )
        if exec1 and not WorkflowTask.objects.filter(execution=exec1).exists():
            WorkflowTask.objects.create(
                execution=exec1,
                step=steps.get("collect"),
                title="Collect market bins",
                description="Pick up and empty the two market skip bins.",
                assigned_to=people.get("p.nunes"),
                status="PENDING",
                deadline=NOW + timedelta(hours=4),
                started_at=NOW - timedelta(hours=3),
            )
        exec2, _ = add_execution(
            wf_waste,
            "end",
            ExecutionStatus.COMPLETED,
            NOW - timedelta(days=5),
            NOW - timedelta(days=4),
        )
        if exec2 and not WorkflowTask.objects.filter(execution=exec2).exists():
            WorkflowTask.objects.create(
                execution=exec2,
                step=steps.get("collect"),
                title="Collect street bins",
                description="Weekly street bin collection round.",
                assigned_to=people.get("p.nunes"),
                status="DONE",
                deadline=NOW - timedelta(days=4),
                started_at=NOW - timedelta(days=5),
                completed_at=NOW - timedelta(days=4, hours=2),
            )

        wf_water = Workflow.objects.filter(name="Water Maintenance Request").first()
        exec3, wsteps = add_execution(
            wf_water,
            "repair",
            ExecutionStatus.RUNNING,
            NOW - timedelta(days=1),
        )
        if exec3 and not WorkflowTask.objects.filter(execution=exec3).exists():
            WorkflowTask.objects.create(
                execution=exec3,
                step=wsteps.get("repair"),
                title="Repair the school leak",
                description="Excavate and clamp the leaking main in front of the school.",
                assigned_to=people.get("p.nunes"),
                status="IN_PROGRESS",
                deadline=NOW + timedelta(hours=6),
                started_at=NOW - timedelta(days=1),
            )
        exec4, _ = add_execution(
            wf_water,
            "end",
            ExecutionStatus.COMPLETED,
            NOW - timedelta(days=12),
            NOW - timedelta(days=11),
        )
        if exec4 and not WorkflowTask.objects.filter(execution=exec4).exists():
            WorkflowTask.objects.create(
                execution=exec4,
                step=wsteps.get("repair"),
                title="Hydrant replacement",
                description="Replace the seized public hydrant on Praça do Município.",
                assigned_to=people.get("p.nunes"),
                status="DONE",
                deadline=NOW - timedelta(days=11),
                started_at=NOW - timedelta(days=12),
                completed_at=NOW - timedelta(days=11, hours=3),
            )

    # ------------------------------------------------------------------- events
    def _seed_events(self, services, people):
        if Event.objects.exists():
            return
        wm = services["Waste Management"]
        Event.objects.create(
            organization=wm.organization,
            service=wm,
            event_type="request.created",
            subject=Request.objects.first(),
            actor=people.get("a.duarte"),
            metadata={"channel": "seed"},
        )
        Event.objects.create(
            organization=wm.organization,
            service=wm,
            event_type="incident.opened",
            subject=Incident.objects.first(),
            actor=people.get("b.lopes"),
            metadata={"channel": "seed"},
        )

    # ------------------------------------------------------------------- audit
    def _seed_audit(self, services, people):
        if AuditLog.objects.exists():
            return
        sm_user = people.get("a.duarte")
        AuditLog.objects.create(
            action="seed.default_dataset",
            actor_person=sm_user,
            actor_user=sm_user.user if sm_user else None,
            organization=services["Waste Management"].organization,
            service=services["Waste Management"],
            object=Request.objects.first(),
            new_value={"note": "Default dataset seeded."},
        )
