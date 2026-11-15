"""Demo data for the parish of Santa Maria, São Pedro e Matacães: service
offerings of Serviços Gerais, Serviços Educativos and Serviços Sociais e
Associativismo, and citizen requests on them (for the citizen portal).

Called by ``seed_default_data``; every function is idempotent (stable names,
``get_or_create`` and "skip if it already exists").
"""

from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.access.models import Role
from apps.offerings.models import (
    ExecutionNote,
    FormFieldType,
    OfferingExecution,
    OfferingFormField,
    OfferingStatus,
    OfferingStep,
    OfferingTransition,
    ServiceOffering,
    ServiceOfferingType,
    StepType,
    TransitionOutcome,
)
from apps.people.models import Person
from apps.requests.models import Priority, Request, RequestAttachment

T = FormFieldType

# Offering type codes added for these services (the seed creates the others).
EXTRA_OFFERING_TYPES = [("EDUCATION", "Educação"), ("CERTIFICATE", "Atestado / certidão")]

# (service, name, type, manager, priority, description, work step, [questions])
# question = (key, label, type, required, help_text, options)
OFFERINGS = [
    # ---------------------------------------------------------- Serviços Gerais
    (
        "Serviços Gerais",
        "Atestado de residência",
        "CERTIFICATE",
        "m.silva",
        Priority.MEDIUM,
        "Emissão de atestado de residência para quem vive na freguesia.",
        "Emitir atestado",
        [
            ("morada", "Morada", T.ADDRESS, True, "Morada onde reside", []),
            (
                "finalidade",
                "Finalidade",
                T.CHOICE,
                True,
                "",
                ["Escola", "Finanças", "Segurança Social", "Banco", "Outra"],
            ),
            ("agregado", "Número de pessoas no agregado", T.NUMBER, False, "", []),
            ("identificacao", "Documento de identificação", T.FILE, True, "Cartão de Cidadão", []),
        ],
    ),
    (
        "Serviços Gerais",
        "Registo e licenciamento de canídeo",
        "ADMINISTRATIVE",
        "m.silva",
        Priority.LOW,
        "Registo e licença anual de cães e gatos residentes na freguesia.",
        "Registar animal",
        [
            ("nome-animal", "Nome do animal", T.TEXT, True, "", []),
            ("especie", "Espécie", T.CHOICE, True, "", ["Cão", "Gato"]),
            ("microchip", "Número do microchip", T.TEXT, True, "15 dígitos", []),
            ("vacina", "Boletim de vacinas", T.FILE, True, "", []),
        ],
    ),
    (
        "Serviços Gerais",
        "Reserva de sala ou equipamento",
        "ADMINISTRATIVE",
        "m.silva",
        Priority.LOW,
        "Reserva do auditório, salas da sede ou equipamento da junta (mesas, cadeiras, tenda).",
        "Preparar reserva",
        [
            (
                "espaco",
                "Espaço ou equipamento",
                T.CHOICE,
                True,
                "",
                ["Auditório", "Sala de reuniões", "Mesas e cadeiras", "Tenda"],
            ),
            ("data", "Data pretendida", T.DATE, True, "", []),
            ("participantes", "Número de participantes", T.NUMBER, False, "", []),
            ("contacto", "Contacto", T.PHONE, True, "", []),
        ],
    ),
    # ------------------------------------------------------- Serviços Educativos
    (
        "Serviços Educativos",
        "Inscrição nas atividades de férias",
        "EDUCATION",
        "r.costa",
        Priority.MEDIUM,
        "Inscrição de crianças dos 6 aos 12 anos no programa de atividades de férias da freguesia.",
        "Confirmar inscrição",
        [
            ("nome-crianca", "Nome da criança", T.TEXT, True, "", []),
            ("data-nascimento", "Data de nascimento", T.DATE, True, "", []),
            ("escola", "Escola que frequenta", T.TEXT, True, "", []),
            (
                "periodo",
                "Períodos",
                T.MULTICHOICE,
                True,
                "",
                ["Natal", "Páscoa", "Verão — julho", "Verão — agosto"],
            ),
            ("alergias", "Tem alergias ou necessidades especiais?", T.BOOLEAN, True, "", []),
            ("observacoes", "Observações", T.TEXTAREA, False, "", []),
        ],
    ),
    (
        "Serviços Educativos",
        "Pedido de transporte escolar",
        "EDUCATION",
        "r.costa",
        Priority.HIGH,
        "Transporte entre a residência e a escola para alunos do 1.º ciclo da freguesia.",
        "Organizar transporte",
        [
            ("nome-aluno", "Nome do aluno", T.TEXT, True, "", []),
            (
                "escola",
                "Escola",
                T.CHOICE,
                True,
                "",
                ["EB Matacães", "EB São Pedro", "EB Santa Maria"],
            ),
            ("morada", "Morada de recolha", T.ADDRESS, True, "", []),
            ("declaracao", "Declaração de matrícula", T.FILE, True, "", []),
        ],
    ),
    (
        "Serviços Educativos",
        "Apoio para material escolar",
        "SUPPORT",
        "r.costa",
        Priority.MEDIUM,
        "Kit de material escolar para alunos de famílias com escalão A ou B.",
        "Entregar material",
        [
            ("nome-aluno", "Nome do aluno", T.TEXT, True, "", []),
            ("ano", "Ano de escolaridade", T.CHOICE, True, "", ["1.º", "2.º", "3.º", "4.º"]),
            ("escalao", "Escalão da ação social escolar", T.CHOICE, True, "", ["A", "B"]),
            ("comprovativo", "Comprovativo do escalão", T.FILE, True, "", []),
        ],
    ),
    # ------------------------------------------- Serviços Sociais e Associativismo
    (
        "Serviços Sociais e Associativismo",
        "Cabaz alimentar",
        "SUPPORT",
        "m.silva",
        Priority.HIGH,
        "Apoio alimentar mensal a agregados em situação de carência económica.",
        "Entregar cabaz",
        [
            ("agregado", "Número de pessoas no agregado", T.NUMBER, True, "", []),
            ("rendimento", "Rendimento mensal do agregado (€)", T.NUMBER, True, "", []),
            ("morada", "Morada", T.ADDRESS, True, "", []),
            ("comprovativo", "Comprovativo de rendimentos", T.FILE, True, "", []),
            ("contacto", "Contacto", T.PHONE, True, "", []),
        ],
    ),
    (
        "Serviços Sociais e Associativismo",
        "Apoio a associação",
        "SUPPORT",
        "m.silva",
        Priority.MEDIUM,
        "Apoio financeiro ou logístico a associações culturais, desportivas e "
        "recreativas da freguesia.",
        "Prestar apoio",
        [
            ("associacao", "Nome da associação", T.TEXT, True, "", []),
            ("nif", "NIPC da associação", T.TEXT, True, "", []),
            (
                "tipo-apoio",
                "Tipo de apoio",
                T.MULTICHOICE,
                True,
                "",
                ["Financeiro", "Transporte", "Cedência de espaço", "Material"],
            ),
            ("atividade", "Atividade a apoiar", T.TEXTAREA, True, "", []),
            ("plano", "Plano de atividades", T.FILE, False, "", []),
        ],
    ),
    (
        "Serviços Sociais e Associativismo",
        "Teleassistência sénior",
        "SUPPORT",
        "m.silva",
        Priority.HIGH,
        "Instalação de equipamento de teleassistência para pessoas idosas que vivem sozinhas.",
        "Instalar equipamento",
        [
            ("nome-beneficiario", "Nome do beneficiário", T.TEXT, True, "", []),
            ("idade", "Idade", T.NUMBER, True, "", []),
            ("vive-sozinho", "Vive sozinho(a)?", T.BOOLEAN, True, "", []),
            ("morada", "Morada", T.ADDRESS, True, "", []),
            ("contacto-familiar", "Contacto de um familiar", T.PHONE, False, "", []),
        ],
    ),
]

# Answers for the existing generic "Apoio social" offering.
APOIO_SOCIAL_FORM = [
    (
        "tipo",
        "Tipo de apoio",
        T.CHOICE,
        True,
        "",
        ["Alimentar", "Habitação", "Saúde", "Outro"],
    ),
    ("situacao", "Descreva a situação", T.TEXTAREA, True, "", []),
    ("contacto", "Contacto", T.PHONE, True, "", []),
]


def _create_flow(offering, work_name, role_sm, role_op):
    steps = [
        ("start", "Início", StepType.START, 10, None, None),
        ("analyse", "Analisar pedido", StepType.TASK, 20, role_sm, 1440),
        ("work", work_name, StepType.TASK, 30, role_op, 2880),
        ("close", "Validar e encerrar", StepType.APPROVAL, 40, role_sm, 1440),
        ("end", "Fim", StepType.END, 50, None, None),
    ]
    created = []
    for code, name, step_type, order, role, sla in steps:
        step, _created = OfferingStep.objects.get_or_create(
            offering=offering,
            code=code,
            defaults={
                "name": name,
                "step_type": step_type,
                "order": order,
                "responsible_role": role,
                "sla_minutes": sla,
            },
        )
        created.append(step)
    for from_step, to_step in zip(created, created[1:], strict=False):
        OfferingTransition.objects.update_or_create(
            offering=offering,
            from_step=from_step,
            outcome=TransitionOutcome.SUCCESS,
            defaults={"to_step": to_step, "order": 10},
        )


def _create_form(offering, questions):
    if offering.form_fields.exists():
        return
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


def seed_parish_offerings(services, people):
    """Offerings (flow + request form) for the three parish services."""
    from apps.offerings.builder import refresh_offering_sla

    for code, name in EXTRA_OFFERING_TYPES:
        ServiceOfferingType.objects.get_or_create(code=code, defaults={"name": name})
    role_sm = Role.objects.get(code="SERVICE_MANAGER")
    role_op = Role.objects.get(code="OPERATOR")
    for service_name, name, type_code, manager, priority, desc, work, questions in OFFERINGS:
        offering, created = ServiceOffering.objects.get_or_create(
            service=services[service_name],
            name=name,
            version=1,
            defaults={
                "description": desc,
                "status": OfferingStatus.ACTIVE,
                "on_request": True,
                "default_priority": priority,
                "created_by": people.get(manager),
                "offering_type": ServiceOfferingType.objects.get(code=type_code),
            },
        )
        if created:
            _create_flow(offering, work, role_sm, role_op)
        _create_form(offering, questions)
        refresh_offering_sla(offering)

    apoio = ServiceOffering.objects.filter(
        service=services["Serviços Sociais e Associativismo"], name="Apoio social", version=1
    ).first()
    # Published offerings are immutable in the editor; the seed only adds
    # questions to its own offering while no request has used it.
    if apoio is not None and not apoio.requests.exists():
        _create_form(apoio, APOIO_SOCIAL_FORM)


# --------------------------------------------------------------- requests
ADDRESS = {"street": "Rua da Liberdade, 12", "postal_code": "2560-374", "locality": "Matacães"}

# (citizen email, offering name, title, description, days ago, answers,
#  file field or None, [(author username or None = citizen, message)])
CITIZEN_REQUESTS = [
    (
        "joana.ribeiro@example.pt",
        "Atestado de residência",
        "Atestado para a matrícula escolar",
        "Preciso do atestado para matricular o meu filho.",
        12,
        {"morada": ADDRESS, "finalidade": "Escola", "agregado": "3"},
        "identificacao",
        [("m.silva", "O atestado está pronto. Pode levantá-lo na sede a partir de amanhã.")],
    ),
    (
        "joana.ribeiro@example.pt",
        "Inscrição nas atividades de férias",
        "Inscrição do Tomás — Natal",
        "",
        4,
        {
            "nome-crianca": "Tomás Ribeiro",
            "data-nascimento": "2018-03-14",
            "escola": "EB Matacães",
            "periodo": ["Natal"],
            "alergias": False,
        },
        None,
        [(None, "Ele pode ficar até às 18h?")],
    ),
    (
        "joana.ribeiro@example.pt",
        "Cabaz alimentar",
        "Pedido de cabaz alimentar",
        "Estou temporariamente desempregada.",
        1,
        {"agregado": "3", "rendimento": "450", "morada": ADDRESS, "contacto": "912000001"},
        "comprovativo",
        [],
    ),
    (
        "antonio.matos@example.pt",
        "Registo e licenciamento de canídeo",
        "Licença do Bobi",
        "",
        20,
        {"nome-animal": "Bobi", "especie": "Cão", "microchip": "941000024681357"},
        "vacina",
        [],
    ),
    (
        "antonio.matos@example.pt",
        "Teleassistência sénior",
        "Teleassistência para a minha mãe",
        "A minha mãe tem 84 anos e vive sozinha em São Pedro.",
        6,
        {
            "nome-beneficiario": "Maria de Lurdes Matos",
            "idade": "84",
            "vive-sozinho": True,
            "morada": {
                "street": "Travessa do Moinho, 3",
                "postal_code": "2560-221",
                "locality": "São Pedro",
            },
            "contacto-familiar": "912000002",
        },
        None,
        [("m.silva", "Vamos agendar uma visita para a instalação na próxima semana.")],
    ),
    (
        "sofia.carvalho@example.pt",
        "Pedido de transporte escolar",
        "Transporte para a EB São Pedro",
        "",
        9,
        {
            "nome-aluno": "Leonor Carvalho",
            "escola": "EB São Pedro",
            "morada": {
                "street": "Rua das Flores, 45",
                "postal_code": "2560-310",
                "locality": "Matacães",
            },
        },
        "declaracao",
        [
            ("r.costa", "Falta a declaração de matrícula atualizada."),
            (None, "Já enviei por email."),
        ],
    ),
    (
        "sofia.carvalho@example.pt",
        "Apoio para material escolar",
        "Kit escolar — 2.º ano",
        "",
        3,
        {"nome-aluno": "Leonor Carvalho", "ano": "2.º", "escalao": "B"},
        "comprovativo",
        [],
    ),
    (
        "rui.gaspar@example.pt",
        "Apoio a associação",
        "Torneio de futsal de Natal",
        "Pedido de apoio para o torneio da Associação Recreativa de Matacães.",
        15,
        {
            "associacao": "Associação Recreativa de Matacães",
            "nif": "501234567",
            "tipo-apoio": ["Transporte", "Material"],
            "atividade": "Torneio de futsal juvenil, 20 a 22 de dezembro.",
        },
        "plano",
        [("m.silva", "Podemos ceder a carrinha no dia 20. Confirma o horário?")],
    ),
    (
        "rui.gaspar@example.pt",
        "Reserva de sala ou equipamento",
        "Mesas e cadeiras para a festa de Natal",
        "",
        2,
        {
            "espaco": "Mesas e cadeiras",
            "data": "2026-12-19",
            "participantes": "80",
            "contacto": "912000004",
        },
        None,
        [],
    ),
    (
        "helena.sousa@example.pt",
        "Apoio social",
        "Apoio na renda da casa",
        "",
        7,
        {
            "tipo": "Habitação",
            "situacao": "Aumento da renda e baixa médica prolongada.",
            "contacto": "912000005",
        },
        None,
        [],
    ),
    (
        "helena.sousa@example.pt",
        "Atestado de residência",
        "Atestado para as Finanças",
        "",
        25,
        {"morada": ADDRESS, "finalidade": "Finanças", "agregado": "1"},
        "identificacao",
        [],
    ),
]


def _example_pdf():
    from apps.common.management.commands.seed_default_data import Command

    return Command._example_pdf()


def seed_citizen_requests(people):
    """Citizen requests on the parish offerings, with a running execution,
    answers, a demo attachment and a few Comments."""
    from apps.offerings.execution import start_request_execution
    from apps.offerings.request_link import sync_request_status

    now = timezone.now()
    for (
        email,
        offering_name,
        title,
        description,
        days,
        answers,
        file_key,
        messages,
    ) in CITIZEN_REQUESTS:
        citizen = Person.objects.filter(email=email, user__isnull=False).first()
        offering = ServiceOffering.objects.filter(
            name=offering_name, version=1, status=OfferingStatus.ACTIVE
        ).first()
        if citizen is None or offering is None:
            continue
        if Request.objects.filter(requester=citizen, title=title).exists():
            continue
        with transaction.atomic():
            request = Request.objects.create(
                service=offering.service,
                offering=offering,
                title=title,
                description=description,
                requester=citizen,
                created_by=citizen,
                priority=offering.default_priority,
                form_data=dict(answers),
            )
            execution = start_request_execution(request, citizen)
            if file_key:
                content = _example_pdf()
                attachment = RequestAttachment(
                    request=request,
                    field_key=file_key,
                    original_name="documento-demonstracao.pdf",
                    size=len(content),
                    content_type="application/pdf",
                )
                attachment.file.save(attachment.original_name, ContentFile(content), save=True)
                request.form_data[file_key] = {
                    "attachment": attachment.pk,
                    "name": attachment.original_name,
                }
                request.save(update_fields=["form_data", "updated_at"])
            created = now - timedelta(days=days)
            Request.objects.filter(pk=request.pk).update(created_at=created)
            OfferingExecution.objects.filter(pk=execution.pk).update(started_at=created)
            for index, (author_key, body) in enumerate(messages):
                author = people[author_key].user if author_key else citizen.user
                note = ExecutionNote.objects.create(
                    execution=execution,
                    author=author,
                    body=body,
                    kind=ExecutionNote.Kind.COMMENT,
                )
                ExecutionNote.objects.filter(pk=note.pk).update(
                    created_at=created + timedelta(hours=6 * (index + 1))
                )
            sync_request_status(execution)
