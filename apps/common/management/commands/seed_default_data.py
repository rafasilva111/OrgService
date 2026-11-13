"""Seed a realistic default dataset for local development / demos.

Usage: ``python manage.py seed_default_data [--reset]``

Idempotent: identifiers are stable (codes/emails), so re-running updates
rather than duplicates. Transactional samples (requests, incidents, …) are
only created when the respective table is empty to keep re-runs cheap.

The demo org tree mirrors a real Portuguese geography:

    Portugal (COUNTRY)
    └── Lisboa (DISTRICT)
        └── Torres Vedras (MUNICIPALITY)
            ├── Santa Maria, São Pedro e Matacães (PARISH)
            ├── A dos Cunhados e Maceira (PARISH)
            └── Ramalhal (PARISH)

Every seeded Service is bound to a Service Manager actor; ``--reset`` wipes the
existing domain data first (demo re-seed).

Also bootstraps a superuser (env-driven) and demo accounts, and seeds the RBAC
catalogue by delegating to ``seed_rbac``.
"""

import os
from datetime import UTC, date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.access.models import Role
from apps.access.scoping import ensure_service_manager_role
from apps.audit.models import AuditLog
from apps.changes.models import Change, ChangeApproval
from apps.events.models import Event
from apps.incidents.models import Impact, Incident, IncidentStatus, IncidentUpdate, Urgency
from apps.metrics.models import KPI, SLA, AggregationType, Metric, MetricValue, ScopeType
from apps.offerings.models import (
    ExecutionStatus,
    FormFieldType,
    OfferingExecution,
    OfferingFormField,
    OfferingStatus,
    OfferingStep,
    OfferingTask,
    OfferingTransition,
    RecurrenceFrequency,
    ServiceOffering,
    ServiceOfferingType,
    StepType,
    TaskActivity,
    TaskStatus,
    TransitionOutcome,
)
from apps.organizations.models import Organization, OrganizationMembership
from apps.people.models import Person, PersonType, Skill, get_system_person
from apps.problems.models import Problem
from apps.requests.models import Priority, Request, RequestAttachment, RequestComment, RequestStatus
from apps.resources.models import Equipment, Resource, ResourceAssignment, ResourceType
from apps.services.models import ActorType, Service, ServiceActor, ServiceStatus, ServiceType

NOW = timezone.now()

# Domain tables reset by ``--reset`` (everything except RBAC + auth catalogue).
_RESET_PREFIXES = (
    "organizations_",
    "services_",
    "offerings_",
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


SERVICE_MANAGER_ROLE_KEYS = {
    "Municipal Administration": "MUNICIPAL_ADMIN",
    "IT": "IT",
    "Supplies": "SUPPLIES",
    "Finance": "FINANCE",
    "HR": "HR",
    "Water Supply": "WATER",
    "Sanitation Network": "SANITATION",
    "Waste Management": "WASTE",
    "Road Maintenance": "ROADS",
    "Public Lighting": "LIGHTING",
    "Serviços Gerais": "GENERAL",
    "Serviços Educativos": "EDUCATION",
    "Espaço Público, Infraestruturas e Edifícios": "PUBLIC_SPACE",
    "Serviços Sociais e Associativismo": "SOCIAL",
    "Cemitérios": "CEMETERIES",
    "Limpeza Urbana e Espaços Verdes": "CLEANING",
}


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
        self._seed_offering_types()
        self._seed_skills()
        self._seed_offerings(services, people)
        self._seed_incidents(services, people)
        self._seed_problems(services, people)
        self._seed_changes(services, people)
        self._seed_resources(orgs, services, people)
        self._seed_metrics(services)
        self._seed_wf_executions(people)
        self._seed_cemetery_executions(services["Cemitérios"], people["p.lopes"])
        from apps.offerings.request_link import attach_missing_requests

        attach_missing_requests()
        self._seed_cemetery_request_answers(services["Cemitérios"])
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
        person, _created = Person.objects.get_or_create(
            email=email,
            defaults={
                "full_name": "System Administrator",
                "person_type": PersonType.EMPLOYEE,
                "user": user,
                "active": not user.is_superuser,
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
                "santa-maria-sao-pedro-matacaes",
                "Santa Maria, São Pedro e Matacães",
                "PARISH",
                "torres-vedras",
                "Union of the parishes of Santa Maria, São Pedro and Matacães.",
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
            ("Municipal Administration", "torres-vedras", ServiceStatus.OPERATIONAL, "management"),
            ("Water Supply", "torres-vedras", ServiceStatus.DEGRADED, "processes"),
            ("Sanitation Network", "torres-vedras", ServiceStatus.OPERATIONAL, "processes"),
            ("Waste Management", "torres-vedras", ServiceStatus.OPERATIONAL, "processes"),
            ("Road Maintenance", "torres-vedras", ServiceStatus.OPERATIONAL, "processes"),
            ("Public Lighting", "torres-vedras", ServiceStatus.OPERATIONAL, "processes"),
            (
                "Serviços Gerais",
                "santa-maria-sao-pedro-matacaes",
                ServiceStatus.OPERATIONAL,
                "management",
            ),
            (
                "Serviços Educativos",
                "santa-maria-sao-pedro-matacaes",
                ServiceStatus.OPERATIONAL,
                "hr",
            ),
            (
                "Espaço Público, Infraestruturas e Edifícios",
                "santa-maria-sao-pedro-matacaes",
                ServiceStatus.OPERATIONAL,
                "processes",
            ),
            (
                "Serviços Sociais e Associativismo",
                "santa-maria-sao-pedro-matacaes",
                ServiceStatus.OPERATIONAL,
                "hr",
            ),
            (
                "Cemitérios",
                "santa-maria-sao-pedro-matacaes",
                ServiceStatus.OPERATIONAL,
                "processes",
            ),
            (
                "Limpeza Urbana e Espaços Verdes",
                "santa-maria-sao-pedro-matacaes",
                ServiceStatus.OPERATIONAL,
                "processes",
            ),
        ]
        services = {}
        for name, org_key, status, type_code in catalog:
            org = orgs[org_key]
            service_type = ServiceType.objects.get(code=type_code)
            service, _created = Service.objects.get_or_create(
                organization=org,
                name=name,
                defaults={
                    "description": f"Service of {org.name}: {name}.",
                    "status": status,
                    "service_type": service_type,
                },
            )
            if service.status != status:
                service.status = status
                service.save()
            if service.service_type != service_type:
                service.service_type = service_type
                service.save()
            services[name] = service

        # Create department children under Municipal Administration (Management type).
        parent_svc = services["Municipal Administration"]
        dept_info = [
            ("IT", "it", "j.fernandes"),
            ("Supplies", "supplies", "n.barreto"),
            ("Finance", "finance", "t.pereira"),
            ("HR", "hr", "m.silva"),
        ]
        for dept_name, type_code, username in dept_info:
            dept_type = ServiceType.objects.get(code=type_code)
            dept, created = Service.objects.get_or_create(
                organization=parent_svc.organization,
                name=dept_name,
                defaults={
                    "description": f"Department of {parent_svc.name}.",
                    "status": parent_svc.status,
                    "service_type": dept_type,
                    "parent": parent_svc,
                },
            )
            if dept.service_type != dept_type:
                dept.service_type = dept_type
                dept.save()
            services[dept_name] = dept
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
            ("a.santos", "André Santos", "andre.santos@torres-vedras.local", "OPERATOR"),
            ("c.oliveira", "Carla Oliveira", "carla.oliveira@torres-vedras.local", "OPERATOR"),
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
            person, _created = Person.objects.get_or_create(
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

        # Membership roles are seeded below; service-scoped SERVICE_MANAGER
        # memberships are added from the manager actor mapping.
        memberships = [
            ("r.martins", tv, role_exec),
            ("l.rodrigues", tv, role_om),
            ("m.mendes", tv, role_rm),
            ("b.lopes", tv, role_im),
            ("h.ferreira", tv, role_pm),
            ("i.cardoso", tv, role_an),
            ("p.nunes", orgs["santa-maria-sao-pedro-matacaes"], role_op),
            ("a.santos", orgs["santa-maria-sao-pedro-matacaes"], role_op),
            ("c.oliveira", orgs["santa-maria-sao-pedro-matacaes"], role_op),
        ]
        for username, org, role in memberships:
            OrganizationMembership.objects.get_or_create(
                person=people[username],
                organization=org,
                role=role,
                defaults={"active": True},
            )

        # Every service needs a Service Manager actor <-> person.
        managers = {
            "Municipal Administration": "r.martins",
            "IT": "j.fernandes",
            "Supplies": "n.barreto",
            "Finance": "t.pereira",
            "HR": "m.silva",
            "Water Supply": "j.fernandes",
            "Sanitation Network": "n.barreto",
            "Waste Management": "a.duarte",
            "Road Maintenance": "t.pereira",
            "Public Lighting": "s.almeida",
            "Serviços Gerais": "m.silva",
            "Serviços Educativos": "r.costa",
            "Espaço Público, Infraestruturas e Edifícios": "p.lopes",
            "Serviços Sociais e Associativismo": "m.silva",
            "Cemitérios": "p.lopes",
            "Limpeza Urbana e Espaços Verdes": "p.lopes",
        }
        for svc_name, username in managers.items():
            ServiceActor.objects.get_or_create(
                service=services[svc_name],
                role=role_sm,
                person=people[username],
                defaults={"actor_type": ActorType.INTERNAL, "active": True},
            )
        # Extra actors for department services (inherited roles from parent).
        extra_actors = [
            ("Water Supply", "l.rodrigues", role_om),
            ("Waste Management", "p.nunes", role_op),
            ("Road Maintenance", "p.nunes", role_op),
            ("IT", "l.rodrigues", role_om),
            ("Finance", "a.duarte", role_sm),
            ("Supplies", "n.barreto", role_sm),
            ("HR", "m.silva", role_sm),
            ("Serviços Educativos", "m.mendes", role_rm),
            ("Cemitérios", "p.nunes", role_op),
            ("Cemitérios", "a.santos", role_op),
            ("Cemitérios", "c.oliveira", role_op),
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

        # Each Service Manager actor gets a service-scoped clone of the
        # SERVICE_MANAGER role (every permission scoped SPECIFIC_SERVICE) and a
        # membership at the service's organization, so they only reach the
        # services they manage.
        sm_actors = ServiceActor.objects.filter(
            service__in=services.values(), role=role_sm, active=True, person__isnull=False
        ).select_related("service", "person")
        for actor in sm_actors:
            scoped_role = ensure_service_manager_role(
                role_sm, actor.service, key=SERVICE_MANAGER_ROLE_KEYS.get(actor.service.name)
            )
            OrganizationMembership.objects.get_or_create(
                person=actor.person,
                organization=actor.service.organization,
                role=scoped_role,
                defaults={"active": True},
            )
        return people

    def _service_by_name(self, name):
        return Service.objects.get(name=name)

    # --------------------------------------------------- service offering types
    def _seed_offering_types(self):
        catalog = [
            ("MAINTENANCE", "Manutenção"),
            ("COLLECTION", "Recolha"),
            ("CLEANING", "Limpeza"),
            ("FUNERAL", "Serviço funerário"),
            ("SUPPORT", "Apoio ao munícipe"),
            ("ADMINISTRATIVE", "Pedido administrativo"),
        ]
        for code, name in catalog:
            ServiceOfferingType.objects.get_or_create(code=code, defaults={"name": name})

    # ------------------------------------------------------------------- skills
    def _seed_skills(self):
        for code, name in [
            ("vehicle_operator", "Vehicle Operator"),
            ("electrician", "Electrician"),
            ("plumber", "Plumber"),
            ("hse", "HSE Officer"),
        ]:
            Skill.objects.get_or_create(code=code, defaults={"name": name})

    # ---------------------------------------------------------------- offerings
    def _seed_offerings(self, services, people):
        role_op = Role.objects.get(code="OPERATOR")
        role_sm = Role.objects.get(code="SERVICE_MANAGER")

        def build(
            name,
            service,
            description,
            steps,
            on_request=True,
            schedule=None,
            created_by="a.duarte",
            offering_type=None,
            flow=None,
        ):
            # ``flow``: {code: {"next": code, "on_error": code, "message": str}};
            # steps without an explicit ``next`` follow the list order.
            flow = flow or {}
            otype = ServiceOfferingType.objects.get(code=offering_type) if offering_type else None
            wf, _created = ServiceOffering.objects.get_or_create(
                service=service,
                name=name,
                version=1,
                defaults={
                    "description": description,
                    "status": OfferingStatus.ACTIVE,
                    "on_request": on_request,
                    "created_by": people.get(created_by),
                    "offering_type": otype,
                },
            )
            if schedule and not wf.on_schedule:
                # Recurring as well: {frequency, interval, month_day, start, ...}.
                wf.on_schedule = True
                for key, value in schedule.items():
                    setattr(wf, f"recurrence_{key}", value)
                wf.save()
            if otype is not None and wf.offering_type_id is None:
                wf.offering_type = otype
                wf.save(update_fields=["offering_type", "updated_at"])
            created = {}
            for code, sname, step_type, order, role, inputs, sla in steps:
                step, _created = OfferingStep.objects.get_or_create(
                    offering=wf,
                    code=code,
                    defaults={
                        "name": sname,
                        "step_type": step_type,
                        "order": order,
                        "responsible_role": role,
                        "required_inputs": inputs,
                        "sla_minutes": sla,
                        "message": flow.get(code, {}).get("message", ""),
                    },
                )
                created[code] = step
            ordered = [created[code] for code, *_rest in sorted(steps, key=lambda s: s[3])]
            for index, from_step in enumerate(ordered[:-1]):
                rules = flow.get(from_step.code, {})
                edges = {
                    TransitionOutcome.SUCCESS: created[rules["next"]]
                    if "next" in rules
                    else ordered[index + 1],
                }
                if "on_error" in rules:
                    edges[TransitionOutcome.FAILURE] = created[rules["on_error"]]
                for outcome, to_step in edges.items():
                    OfferingTransition.objects.update_or_create(
                        offering=wf,
                        from_step=from_step,
                        outcome=outcome,
                        defaults={
                            "to_step": to_step,
                            "order": 10 if outcome == TransitionOutcome.SUCCESS else 20,
                        },
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
            offering_type="COLLECTION",
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
            created_by="j.fernandes",
            offering_type="MAINTENANCE",
        )

        # One default offering for every other service that receives requests.
        def simple(name, service_name, description, work, manager, offering_type):
            build(
                name,
                services[service_name],
                description,
                [
                    ("start", "Start", StepType.START, 10, None, [], None),
                    ("triage", "Triage", StepType.TASK, 20, role_sm, [], 1440),
                    ("work", work, StepType.TASK, 30, role_op, [], None),
                    ("verify", "Verify", StepType.APPROVAL, 40, role_sm, [], 1440),
                    ("end", "End", StepType.END, 50, None, [], None),
                ],
                created_by=manager,
                offering_type=offering_type,
            )

        simple(
            "Road Repair Request",
            "Road Maintenance",
            "Potholes, damaged pavement and road signage repairs.",
            "Repair Road",
            "t.pereira",
            "MAINTENANCE",
        )
        simple(
            "Street Light Repair",
            "Public Lighting",
            "Faulty or damaged street lights and new lighting points.",
            "Repair Light",
            "s.almeida",
            "MAINTENANCE",
        )
        simple(
            "Sewer Maintenance Request",
            "Sanitation Network",
            "Blockages, odours and repairs on the sewer network.",
            "Clear / Repair Sewer",
            "n.barreto",
            "MAINTENANCE",
        )
        simple(
            "General Administrative Request",
            "Municipal Administration",
            "Certificates, licences and other administrative requests.",
            "Process Request",
            "r.martins",
            "ADMINISTRATIVE",
        )
        simple(
            "Limpeza de espaço público",
            "Limpeza Urbana e Espaços Verdes",
            "Limpeza de ruas, largos e espaços verdes da freguesia.",
            "Executar limpeza",
            "p.lopes",
            "CLEANING",
        )
        simple(
            "Manutenção de espaço público",
            "Espaço Público, Infraestruturas e Edifícios",
            "Manutenção de espaços públicos, infraestruturas e edifícios da freguesia, "
            "incluindo a preparação de sepulturas.",
            "Executar trabalho",
            "p.lopes",
            "MAINTENANCE",
        )
        simple(
            "Apoio social",
            "Serviços Sociais e Associativismo",
            "Pedidos de apoio social e às associações da freguesia.",
            "Prestar apoio",
            "m.silva",
            "SUPPORT",
        )

        # --- Cemitérios (Santa Maria, São Pedro e Matacães)
        cemiterios = services["Cemitérios"]
        build(
            "Limpar cemitério",
            cemiterios,
            "Limpeza periódica do cemitério: remoção de ervas e resíduos, limpeza de "
            "arruamentos e zonas comuns.",
            [
                ("start", "Início", StepType.START, 10, None, [], None),
                ("plan", "Planear limpeza", StepType.TASK, 20, role_sm, [], 1440),
                (
                    "clean",
                    "Executar limpeza",
                    StepType.TASK,
                    30,
                    role_op,
                    ["equipa", "ferramentas"],
                    480,
                ),
                ("verify", "Verificar limpeza", StepType.APPROVAL, 40, role_sm, [], 1440),
                ("end", "Fim", StepType.END, 50, None, [], None),
            ],
            # Monthly cleaning on day 1, plus extra cleanings on request.
            schedule={
                "frequency": RecurrenceFrequency.MONTHLY,
                "interval": 1,
                "month_day": 1,
                "start": date(2026, 12, 1),
            },
            created_by="p.lopes",
            offering_type="CLEANING",
        )
        build(
            "Realizar um funeral",
            cemiterios,
            "Inumação no cemitério paroquial: validação da documentação, atribuição e "
            "preparação da sepultura, e realização do funeral.",
            [
                ("start", "Início", StepType.START, 10, None, [], None),
                (
                    "validate",
                    "Validar documentação",
                    StepType.TASK,
                    20,
                    role_sm,
                    ["certidão de óbito", "requerimento"],
                    240,
                ),
                (
                    "assign_grave",
                    "Atribuir sepultura",
                    StepType.APPROVAL,
                    30,
                    role_sm,
                    ["sepultura"],
                    240,
                ),
                ("prepare", "Preparar sepultura", StepType.TASK, 40, role_op, [], 1440),
                ("burial", "Realizar inumação", StepType.TASK, 50, role_op, [], None),
                ("register", "Registar inumação", StepType.TASK, 60, role_sm, [], 1440),
                (
                    "notify_missing_docs",
                    "Notificar requerente",
                    StepType.NOTIFY,
                    65,
                    role_sm,
                    [],
                    60,
                ),
                ("end", "Fim", StepType.END, 70, None, [], None),
            ],
            created_by="p.lopes",
            offering_type="FUNERAL",
            flow={
                # Missing/invalid documents: tell the requester and stop.
                "validate": {"on_error": "notify_missing_docs"},
                "register": {"next": "end"},
                "notify_missing_docs": {
                    "next": "end",
                    "message": "A documentação entregue está incompleta ou inválida. "
                    "Por favor entregue a certidão de óbito e o requerimento em falta "
                    "para podermos realizar o funeral.",
                },
            },
        )
        build(
            "Realizar transladação",
            cemiterios,
            "Transladação de restos mortais: aprovação do requerimento, exumação, "
            "transporte e registo.",
            [
                ("start", "Início", StepType.START, 10, None, [], None),
                (
                    "validate",
                    "Validar requerimento",
                    StepType.TASK,
                    20,
                    role_sm,
                    ["requerimento", "autorização de transladação"],
                    1440,
                ),
                ("approve", "Aprovar transladação", StepType.APPROVAL, 30, role_sm, [], 2880),
                ("schedule", "Agendar exumação", StepType.TASK, 40, role_sm, [], 1440),
                ("exhume", "Realizar exumação", StepType.TASK, 50, role_op, [], None),
                ("transport", "Transportar restos mortais", StepType.TASK, 60, role_op, [], None),
                ("register", "Registar transladação", StepType.TASK, 70, role_sm, [], 1440),
                ("end", "Fim", StepType.END, 80, None, [], None),
            ],
            created_by="p.lopes",
            offering_type="FUNERAL",
        )

        # Steps handed to another service's offering (blank = the step's own offering).
        def delegate(offering_name, step_code, service_name, responsible_offering_name):
            step = OfferingStep.objects.get(
                offering__service=cemiterios,
                offering__name=offering_name,
                offering__version=1,
                code=step_code,
            )
            if step.service_offering_id is None:
                step.service_offering = ServiceOffering.objects.get(
                    service=services[service_name], name=responsible_offering_name, version=1
                )
                # External step: no role of its own (type/SLA come from the offering).
                step.responsible_role = None
                step.save(update_fields=["service_offering", "responsible_role", "updated_at"])

        delegate(
            "Limpar cemitério",
            "clean",
            "Limpeza Urbana e Espaços Verdes",
            "Limpeza de espaço público",
        )
        delegate(
            "Realizar um funeral",
            "prepare",
            "Espaço Público, Infraestruturas e Edifícios",
            "Manutenção de espaço público",
        )

        # Request forms: the questions asked in Novo Pedido for each offering.
        T = FormFieldType
        request_forms = {
            "Realizar um funeral": [
                ("nome-do-falecido", "Nome completo do falecido", T.TEXT, True, "", []),
                ("data-do-obito", "Data do óbito", T.DATE, True, "", []),
                (
                    "tipo-de-sepultura",
                    "Tipo de sepultura",
                    T.CHOICE,
                    True,
                    "",
                    ["Sepultura temporária", "Sepultura perpétua", "Jazigo", "Ossário"],
                ),
                ("certidao-de-obito", "Certidão de óbito", T.FILE, True, "", []),
                ("velorio", "Pretende velório na capela?", T.BOOLEAN, True, "", []),
                ("contacto", "Contacto do requerente", T.PHONE, True, "Telemóvel ou telefone", []),
                ("observacoes", "Observações", T.TEXTAREA, False, "", []),
            ],
            "Realizar transladação": [
                ("nome-do-falecido", "Nome completo do falecido", T.TEXT, True, "", []),
                ("cemiterio-de-destino", "Morada do cemitério de destino", T.ADDRESS, True, "", []),
                ("autorizacao", "Autorização de transladação", T.FILE, True, "", []),
                ("contacto", "Contacto do requerente", T.PHONE, True, "", []),
            ],
            "Limpar cemitério": [
                (
                    "zonas",
                    "Zonas a limpar",
                    T.MULTICHOICE,
                    True,
                    "",
                    ["Arruamentos", "Zonas verdes", "Capela", "Sanitários"],
                ),
                ("email", "Email para resposta", T.EMAIL, False, "", []),
            ],
            "Road Repair Request": [
                ("localizacao", "Localização", T.ADDRESS, True, "Onde está o problema", []),
                ("buracos", "Número aproximado de buracos", T.NUMBER, False, "", []),
            ],
        }
        for offering_name, questions in request_forms.items():
            offering = ServiceOffering.objects.filter(name=offering_name).first()
            if offering is None or offering.form_fields.exists():
                continue
            for index, (key, label, ftype, required, help_text, options) in enumerate(questions):
                OfferingFormField.objects.create(
                    offering=offering,
                    key=key,
                    label=label,
                    field_type=ftype,
                    required=required,
                    help_text=help_text,
                    options=options,
                    order=(index + 1) * 10,
                )

        # Total SLAs: responsible offerings first, then copy their totals into the
        # external steps that point at them, then the offerings using them.
        from apps.offerings.builder import refresh_offering_sla

        offerings = list(ServiceOffering.objects.all())
        for offering in offerings:
            refresh_offering_sla(offering)
        for step in OfferingStep.objects.filter(service_offering__isnull=False).select_related(
            "offering", "service_offering"
        ):
            if step.is_external and step.sla_minutes != step.service_offering.sla_minutes:
                step.sla_minutes = step.service_offering.sla_minutes
                step.save(update_fields=["sla_minutes", "updated_at"])
        for offering in offerings:
            refresh_offering_sla(offering)

    # ---------------------------------------------------------------- requests
    def _seed_requests(self, services, people):
        wf_waste = ServiceOffering.objects.filter(name="Waste Collection Request").first()
        wf_water = ServiceOffering.objects.filter(name="Water Maintenance Request").first()
        # (service, title, desc, status, priority, assigned_key, offering or None)
        samples = [
            # --- Waste Management
            (
                services["Waste Management"],
                "New skip bins for the market",
                "Requesting two additional 8m³ skip bins for the municipal market.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "p.nunes",
                wf_waste,
            ),
            (
                services["Waste Management"],
                "Missed collection on Rua de São Miguel",
                "Residents report no collection for the past two weeks on Rua de São Miguel.",
                RequestStatus.NEW,
                Priority.HIGH,
                None,
                wf_waste,
            ),
            (
                services["Waste Management"],
                "Broken skip bin lid",
                "Skip bin at the market square has a damaged lid that cannot be closed securely.",
                RequestStatus.NEW,
                Priority.LOW,
                None,
                wf_waste,
            ),
            (
                services["Waste Management"],
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
                "Borehole inspection on Rua do Forte",
                "Schedule a preventative inspection of the Matacães borehole.",
                RequestStatus.WAITING,
                Priority.LOW,
                None,
                wf_water,
            ),
            (
                services["Water Supply"],
                "Leak in front of the school",
                "Water leaking from the main in front of the primary school; pavers lifting.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "j.fernandes",
                wf_water,
            ),
            (
                services["Water Supply"],
                "New connection for the bakery",
                "Bakery on Rua Augusta requesting a new water connection.",
                RequestStatus.WAITING,
                Priority.MEDIUM,
                None,
                wf_water,
            ),
            (
                services["Water Supply"],
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
                "Sinkhole near the roundabout",
                "Sinkhole forming on the approach to the EN8 roundabout; kernel hazard.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "p.nunes",
                None,
            ),
            (
                services["Road Maintenance"],
                "Repave Rua dos Ciprestes",
                "Surface crumbling on Rua dos Ciprestes; full resurfacing requested.",
                RequestStatus.WAITING,
                Priority.MEDIUM,
                None,
                None,
            ),
            (
                services["Road Maintenance"],
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
                "Streetlight outage on Av. 25 de Abril",
                "Three streetlights out along Avenida 25 de Abril near the stadium.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "t.pereira",
                None,
            ),
            (
                services["Public Lighting"],
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
                "Blocked sewer on Rua do Moinho",
                "Sewer backing up at Rua do Moinho; odours in neighbouring houses.",
                RequestStatus.IN_PROGRESS,
                Priority.HIGH,
                "n.barreto",
                None,
            ),
            (
                services["Sanitation Network"],
                "Replacement of manhole cover",
                "Cracked manhole cover in the middle of Rua da Fonte.",
                RequestStatus.NEW,
                Priority.MEDIUM,
                None,
                None,
            ),
            # --- Limpeza Urbana e Espaços Verdes
            (
                services["Limpeza Urbana e Espaços Verdes"],
                "Tree pruning in Praça do Município",
                "Prune the cork oaks in Praça do Município before the summer.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "r.costa",
                None,
            ),
            (
                services["Limpeza Urbana e Espaços Verdes"],
                "Playground swing repair",
                "One swing broken in the Matacães playground.",
                RequestStatus.NEW,
                Priority.LOW,
                None,
                None,
            ),
            # --- Serviços Sociais e Associativismo
            (
                services["Serviços Sociais e Associativismo"],
                "Rat control campaign",
                "Planning a community rat-control and hygiene awareness campaign.",
                RequestStatus.RESOLVED,
                Priority.MEDIUM,
                "p.nunes",
                None,
            ),
            (
                services["Serviços Sociais e Associativismo"],
                "Mosquito breeding hotspots",
                "Identified standing-water hotspots around the market needing treatment.",
                RequestStatus.NEW,
                Priority.MEDIUM,
                None,
                None,
            ),
            (
                services["Serviços Sociais e Associativismo"],
                "Food safety inspection",
                "Routine inspection of the summer festival food stalls.",
                RequestStatus.WAITING,
                Priority.MEDIUM,
                "i.cardoso",
                None,
            ),
            # --- Limpeza Urbana e Espaços Verdes
            (
                services["Limpeza Urbana e Espaços Verdes"],
                "Illegal dumping on the Matacães road",
                "Furniture and construction debris dumped on the roadside.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "p.nunes",
                None,
            ),
            (
                services["Limpeza Urbana e Espaços Verdes"],
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
                "Archives records request",
                "Citizen requesting urbanism records for a property in the old town.",
                RequestStatus.RESOLVED,
                Priority.LOW,
                None,
                None,
            ),
            (
                services["Municipal Administration"],
                "Licence renewal for street vendor",
                "Renewal of the market license for a vegetable vendor.",
                RequestStatus.IN_PROGRESS,
                Priority.MEDIUM,
                "m.mendes",
                None,
            ),
        ]
        # Samples without an explicit offering use their service's default one.
        default_offering = {}
        for offering in ServiceOffering.objects.filter(status=OfferingStatus.ACTIVE).order_by("pk"):
            default_offering.setdefault(offering.service_id, offering)
        for service, title, desc, status, priority, assigned_key, offering in samples:
            offering = offering or default_offering[service.pk]
            req, _created = Request.objects.get_or_create(
                service=service,
                title=title,
                defaults={
                    "description": desc,
                    "requester": people.get("i.cardoso"),
                    "created_by": people.get("m.mendes"),
                    "status": status,
                    "priority": priority,
                    "assigned_to": people.get(assigned_key) if assigned_key else None,
                    "offering": offering,
                },
            )
            if req.offering_id is None:
                req.offering = offering
                req.save(update_fields=["offering", "updated_at"])
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
                services["Serviços Sociais e Associativismo"],
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
            incident, _created = Incident.objects.get_or_create(
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
            problem, _created = Problem.objects.get_or_create(
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
            change, _created = Change.objects.get_or_create(
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
            resource, _created = widget.objects.get_or_create(
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
            metric, _created = Metric.objects.get_or_create(
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

        kpi, _created = KPI.objects.get_or_create(
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

        if not SLA.objects.filter(offering__isnull=True).exists():
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
    @staticmethod
    def _example_pdf():
        message = b"Documento de demonstracao. Sem valor legal."
        stream = b"BT /F1 14 Tf 50 750 Td (" + message + b") Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        ]
        pdf = b"%PDF-1.4\n"
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref = len(pdf)
        pdf += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
        pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
        pdf += (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
        return pdf

    def _seed_cemetery_request_answers(self, service):
        samples = {
            ("Limpar cemitério", datetime(2026, 11, 9, 9, tzinfo=UTC)): {
                "zonas": ["Arruamentos", "Zonas verdes"],
                "email": "requerente@example.invalid",
            },
            ("Limpar cemitério", datetime(2026, 10, 1, 9, tzinfo=UTC)): {
                "zonas": ["Arruamentos", "Capela"],
                "email": "requerente@example.invalid",
            },
            ("Realizar um funeral", datetime(2026, 11, 10, 10, tzinfo=UTC)): {
                "nome-do-falecido": "Pessoa Exemplo",
                "data-do-obito": "2026-11-08",
                "tipo-de-sepultura": "Sepultura temporária",
                "velorio": True,
                "contacto": "910000000",
                "observacoes": "Dados fictícios para demonstração.",
            },
            ("Realizar um funeral", datetime(2026, 10, 21, 9, tzinfo=UTC)): {
                "nome-do-falecido": "Pessoa Exemplo Dois",
                "data-do-obito": "2026-10-19",
                "tipo-de-sepultura": "Jazigo",
                "velorio": False,
                "contacto": "910000000",
                "observacoes": "Dados fictícios para demonstração.",
            },
            ("Realizar transladação", datetime(2026, 11, 6, 11, tzinfo=UTC)): {
                "nome-do-falecido": "Pessoa Exemplo",
                "cemiterio-de-destino": {
                    "street": "Rua Exemplo, 1",
                    "postal_code": "2560-100",
                    "locality": "Torres Vedras",
                },
                "contacto": "910000000",
            },
            ("Realizar transladação", datetime(2026, 10, 12, 9, tzinfo=UTC)): {
                "nome-do-falecido": "Pessoa Exemplo Dois",
                "cemiterio-de-destino": {
                    "street": "Rua Exemplo, 2",
                    "postal_code": "2560-100",
                    "locality": "Torres Vedras",
                },
                "contacto": "910000000",
            },
        }
        files = {
            "Realizar um funeral": "certidao-de-obito",
            "Realizar transladação": "autorizacao",
        }
        request_type = ContentType.objects.get_for_model(Request)
        for (name, started), answers in samples.items():
            execution = OfferingExecution.objects.filter(
                offering__service=service,
                offering__name=name,
                offering__version=1,
                started_at=started,
                subject_type=request_type,
            ).first()
            if execution is None:
                continue
            with transaction.atomic():
                request = Request.objects.select_for_update().filter(
                    pk=execution.subject_id,
                    offering_id=execution.offering_id,
                    title=f"{name} — #{execution.pk}",
                ).first()
                if request is None or request.form_data or request.attachments.exists():
                    continue
                data = dict(answers)
                key = files.get(name)
                if key:
                    content = self._example_pdf()
                    attachment = RequestAttachment(
                        request=request,
                        field_key=key,
                        original_name="exemplo-demonstracao.pdf",
                        size=len(content),
                        content_type="application/pdf",
                    )
                    attachment.file.save(attachment.original_name, ContentFile(content), save=True)
                    data[key] = {"attachment": attachment.pk, "name": attachment.original_name}
                request.form_data = data
                request.save(update_fields=["form_data", "updated_at"])

    def _seed_cemetery_executions(self, service, manager):
        system_person = get_system_person()
        samples = [
            (
                "Limpar cemitério",
                "clean",
                ExecutionStatus.RUNNING,
                TaskStatus.IN_PROGRESS,
                "Limpeza das zonas comuns",
                datetime(2026, 11, 9, 9, tzinfo=UTC),
                None,
            ),
            (
                "Limpar cemitério",
                "end",
                ExecutionStatus.COMPLETED,
                TaskStatus.DONE,
                "Limpeza mensal do cemitério",
                datetime(2026, 10, 1, 9, tzinfo=UTC),
                datetime(2026, 10, 2, 15, tzinfo=UTC),
            ),
            (
                "Realizar um funeral",
                "assign_grave",
                ExecutionStatus.RUNNING,
                TaskStatus.IN_PROGRESS,
                "Atribuir sepultura para inumação",
                datetime(2026, 11, 10, 10, tzinfo=UTC),
                None,
            ),
            (
                "Realizar um funeral",
                "end",
                ExecutionStatus.COMPLETED,
                TaskStatus.DONE,
                "Registar inumação concluída",
                datetime(2026, 10, 21, 9, tzinfo=UTC),
                datetime(2026, 10, 23, 16, tzinfo=UTC),
            ),
            (
                "Realizar transladação",
                "approve",
                ExecutionStatus.WAITING,
                TaskStatus.BLOCKED,
                "Aprovar pedido de transladação",
                datetime(2026, 11, 6, 11, tzinfo=UTC),
                None,
            ),
            (
                "Realizar transladação",
                "end",
                ExecutionStatus.COMPLETED,
                TaskStatus.DONE,
                "Registar transladação concluída",
                datetime(2026, 10, 12, 9, tzinfo=UTC),
                datetime(2026, 10, 16, 15, tzinfo=UTC),
            ),
        ]
        completed_steps = {
            "Limpar cemitério": ("plan", "clean", "verify"),
            "Realizar um funeral": (
                "validate",
                "assign_grave",
                "prepare",
                "burial",
                "register",
            ),
            "Realizar transladação": (
                "validate",
                "approve",
                "schedule",
                "exhume",
                "transport",
                "register",
            ),
        }
        preceding_steps = {
            ("Limpar cemitério", "clean"): ("plan",),
            ("Realizar um funeral", "assign_grave"): ("validate",),
            ("Realizar transladação", "approve"): ("validate",),
        }
        for name, current_code, status, task_status, title, started, completed in samples:
            offering = ServiceOffering.objects.get(service=service, name=name, version=1)
            steps = {step.code: step for step in offering.steps.all()}
            with transaction.atomic():
                execution, _created = OfferingExecution.objects.get_or_create(
                    offering=offering,
                    started_at=started,
                    defaults={
                        "status": status,
                        "current_step": steps[current_code],
                        "completed_at": completed,
                        "created_by": manager,
                    },
                )
                if not completed and execution.current_step_id != steps[current_code].pk:
                    continue
                if completed:
                    task_codes = completed_steps[name]
                    duration_seconds = int((completed - started).total_seconds())
                    for index, code in enumerate(task_codes):
                        task_started = started + timedelta(
                            seconds=duration_seconds * index // len(task_codes)
                        )
                        task_completed = started + timedelta(
                            seconds=duration_seconds * (index + 1) // len(task_codes)
                        )
                        step = steps[code]
                        task = execution.tasks.filter(step=step).order_by("pk").first()
                        if task is None:
                            task = OfferingTask.objects.create(
                                execution=execution,
                                step=step,
                                title=title if index == len(task_codes) - 1 else step.name,
                                assigned_to=manager,
                                status=TaskStatus.DONE,
                                started_at=task_started,
                                completed_at=task_completed,
                            )
                        elif (
                            task.title == title
                            and task.assigned_to_id == manager.pk
                            and not task.activities.exists()
                        ):
                            task.started_at = task_started
                            task.completed_at = task_completed
                            task.save(update_fields=["started_at", "completed_at", "updated_at"])
                        self._seed_completed_task_history(task, manager, system_person)
                else:
                    prior_codes = preceding_steps.get((name, current_code), ())
                    for index, code in enumerate(prior_codes):
                        task_started = started + timedelta(hours=index)
                        task_completed = task_started + timedelta(hours=1)
                        step = steps[code]
                        task = execution.tasks.filter(step=step).order_by("pk").first()
                        if task is None:
                            task = OfferingTask.objects.create(
                                execution=execution,
                                step=step,
                                title=step.name,
                                assigned_to=manager,
                                status=TaskStatus.DONE,
                                started_at=task_started,
                                completed_at=task_completed,
                            )
                        self._seed_completed_task_history(task, manager, system_person)

                    current_task = execution.tasks.filter(step=steps[current_code]).first()
                    if current_task is None:
                        current_task = OfferingTask.objects.create(
                            execution=execution,
                            step=steps[current_code],
                            title=title,
                            assigned_to=manager,
                            status=task_status,
                            started_at=started + timedelta(hours=len(prior_codes)),
                        )
                    elif (
                        current_task
                        and prior_codes
                        and current_task.title == title
                        and current_task.assigned_to_id == manager.pk
                        and current_task.started_at == started
                        and not current_task.activities.exists()
                    ):
                        current_task.started_at = started + timedelta(hours=len(prior_codes))
                        current_task.save(update_fields=["started_at", "updated_at"])
                    if current_task:
                        self._seed_active_task_history(current_task, manager, system_person)

    @staticmethod
    def _seed_completed_task_history(task, manager, system_person):
        created_at = task.started_at - timedelta(minutes=5)
        history = (
            ("created", system_person, "", created_at),
            ("assigned", manager, manager.full_name, task.started_at - timedelta(minutes=1)),
            ("started", manager, "", task.started_at),
            ("completed", manager, "", task.completed_at),
        )
        for action, actor, detail, timestamp in history:
            Command._ensure_seeded_task_activity(task, action, actor, detail, timestamp)

    @staticmethod
    def _seed_active_task_history(task, manager, system_person):
        created_at = task.started_at - timedelta(minutes=5)
        history = (
            ("created", system_person, "", created_at),
            ("assigned", manager, manager.full_name, task.started_at - timedelta(minutes=1)),
            ("started", manager, "", task.started_at),
        )
        for action, actor, detail, timestamp in history:
            Command._ensure_seeded_task_activity(task, action, actor, detail, timestamp)

    @staticmethod
    def _ensure_seeded_task_activity(task, action, actor, detail, timestamp):
        activity = task.activities.filter(action=action).order_by("pk").first()
        if activity is None:
            activity = TaskActivity.objects.create(
                task=task,
                actor=actor,
                action=action,
                detail=detail,
            )
            TaskActivity.objects.filter(pk=activity.pk).update(created_at=timestamp)
        elif action == "created" and activity.actor_id != actor.pk:
            activity.actor = actor
            activity.save(update_fields=["actor", "updated_at"])

    def _seed_wf_executions(self, people):
        def add_execution(wf, current_code, status, started, completed=None, task_title=""):
            if not wf:
                return
            steps = {s.code: s for s in wf.steps.all()}
            existing = OfferingExecution.objects.filter(
                offering=wf, tasks__title=task_title
            ).order_by("pk").first()
            if existing is not None:
                return existing, steps
            execn, _created = OfferingExecution.objects.get_or_create(
                offering=wf,
                started_at=started,
                defaults={
                    "status": status,
                    "current_step": steps.get(current_code),
                    "completed_at": completed,
                    "created_by": people.get("a.duarte"),
                },
            )
            return execn, steps

        wf_waste = ServiceOffering.objects.filter(name="Waste Collection Request").first()
        exec1, steps = add_execution(
            wf_waste,
            "collect",
            ExecutionStatus.RUNNING,
            NOW - timedelta(hours=3),
            task_title="Collect market bins",
        )
        if exec1 and not OfferingTask.objects.filter(execution=exec1).exists():
            OfferingTask.objects.create(
                execution=exec1,
                step=steps.get("collect"),
                title="Collect market bins",
                description="Pick up and empty the two market skip bins.",
                assigned_to=people.get("p.nunes"),
                status="PENDING",
                deadline=NOW + timedelta(hours=4),
                started_at=NOW - timedelta(hours=3),
            )
        exec2, _created = add_execution(
            wf_waste,
            "end",
            ExecutionStatus.COMPLETED,
            NOW - timedelta(days=5),
            NOW - timedelta(days=4),
            task_title="Collect street bins",
        )
        if exec2 and not OfferingTask.objects.filter(execution=exec2).exists():
            OfferingTask.objects.create(
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

        wf_water = ServiceOffering.objects.filter(name="Water Maintenance Request").first()
        exec3, wsteps = add_execution(
            wf_water,
            "repair",
            ExecutionStatus.RUNNING,
            NOW - timedelta(days=1),
            task_title="Repair the school leak",
        )
        if exec3 and not OfferingTask.objects.filter(execution=exec3).exists():
            OfferingTask.objects.create(
                execution=exec3,
                step=wsteps.get("repair"),
                title="Repair the school leak",
                description="Excavate and clamp the leaking main in front of the school.",
                assigned_to=people.get("p.nunes"),
                status="IN_PROGRESS",
                deadline=NOW + timedelta(hours=6),
                started_at=NOW - timedelta(days=1),
            )
        exec4, _created = add_execution(
            wf_water,
            "end",
            ExecutionStatus.COMPLETED,
            NOW - timedelta(days=12),
            NOW - timedelta(days=11),
            task_title="Hydrant replacement",
        )
        if exec4 and not OfferingTask.objects.filter(execution=exec4).exists():
            OfferingTask.objects.create(
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
