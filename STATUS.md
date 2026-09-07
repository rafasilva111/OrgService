# STATUS.md

> Ficheiro de acompanhamento do processo de construção do projeto.
> Atualizar a cada tarefa concluída / decisão tomada.
> IMPORTANTE: ao alterar o dataset, serviços, roles, contas ou a lógica, atualizar
> também `README.md` (e vice-versa) — ambos são fonte de verdade do comportamento da plataforma.

## Estado global: EM OPERAÇÃO (v4 — Internacionalização EN/PT + Perfil & Definições)

Plano base concluído (modelos, autorização), fase v2 (views RBAC, dashboards, listagens)
e fase v3 (ciclo de vida do catálogo core) concluídas. Fase v4 adicionou **UI bilingue
(inglês por defeito + português)** com preferência de idioma por utilizador
(`apps.profiles.UserProfile.language` + `UserLanguageMiddleware`), páginas **My profile**
e **Settings** ligadas ao dropdown do header, `LANGUAGES`/`LOCALE_PATHS`/`LocaleMiddleware`
em `config/settings.py`, e catálogo compilado `locale/pt/LC_MESSAGES/django.{po,mo}`
(511 msgids + plurais) construído via `polib` (sem `msgfmt`/`xgettext` no host).
**141 testes pytest verdes** e verificação live via Docker/curl concluída.

---

## Linha do tempo

| Data       | Fase               | Descrição                                                                | Estado   |
|------------|--------------------|--------------------------------------------------------------------------|----------|
| 2026-09-06 | Infraestrutura DB  | Docker Compose + `.env` para PostgreSQL 16 criados                       | Feito    |
| 2026-09-06 | Ambiente Python    | venv + Django 5.2.17 + pytest 9.1.1 (requirements.txt)                   | Feito    |
| 2026-09-06 | Esqueleto Django   | manage.py, config/settings, pytest.ini, 13 apps registadas               | Feito    |
| 2026-09-06 | Base comum         | `apps/common`: TimeStampedModel, ActiveModel, LTreeField, authorization  | Feito    |
| 2026-09-06 | people             | Person, Skill, PersonSkill                                               | Feito    |
| 2026-09-06 | organizations      | Organization (ltree), OrganizationMembership, OrganizationUnit           | Feito    |
| 2026-09-06 | access             | Role, Permission, RolePermission, ServiceAccess + `seed_rbac`            | Feito    |
| 2026-09-06 | services           | Service, ServiceCapability(+Assignment), ServiceActor                    | Feito    |
| 2026-09-06 | workflows          | Workflow, WorkflowStep, WorkflowTransition, WorkflowExecution, Task      | Feito    |
| 2026-09-06 | requests           | Request, RequestType, RequestComment                                     | Feito    |
| 2026-09-06 | incidents/problems | Incident, IncidentUpdate, Problem, ProblemIncident                       | Feito    |
| 2026-09-06 | changes            | Change, ChangeApproval                                                   | Feito    |
| 2026-09-06 | resources          | Resource, Asset, Equipment, Material, ResourceAssignment                 | Feito    |
| 2026-09-06 | events + audit     | Event (generic FK), AuditLog (generic FK)                                | Feito    |
| 2026-09-06 | metrics            | Metric, MetricValue, KPI(+KpiMetric), SLA                                | Feito    |
| 2026-09-06 | authorization      | `can()`, `visible()`, `visible_to()` ao nível da base de dados           | Feito    |
| 2026-09-06 | migrações          | makemigrations + migrate (Postgres via Docker), extension `ltree`        | Feito    |
| 2026-09-06 | testes             | pytest: 49 testes verdes (domínios + autorização + seed)                | Feito    |
| 2026-09-06 | documentação       | architecture.md, domain-model.md, authorization.md, ER diagrama Mermaid  | Feito    |
| 2026-09-06 | Views + RBAC       | Mixins `PermissionRequiredMixin` + `platform_codes` em todas as listas/detalhes; scope por organização/serviço (`ScopedDetailView`) | Feito    |
| 2026-09-06 | Dashboards         | Executive / `dashboard:organization_dashboard` / `dashboard:service_dashboard` (chart por estado, KPIs) | Feito    |
| 2026-09-06 | Templates UI       | `templates/partials/*` (header, breadcrumbs, tables datetime), forms com `apex_field` | Feito    |
| 2026-09-06 | Testes UI          | `tests/test_views.py`: redirect anónimo, 403, render listas/detalhes, scope, create perms, bulk mark_closed | Feito    |
| 2026-09-06 | Docker web         | `Dockerfile` + `docker-entrypoint.sh` + serviço `web` no compose (migrate → seed → runserver) | Feito    |
| 2026-09-06 | Dataset padrão     | `seed_default_data` idempotente (orgs, serviços, pessoas, RBAC, samples) + superuser/demo users | Feito    |
| 2026-09-06 | Soft-delete (v3)   | `SoftDeleteModel/SoftDeleteQuerySet/SoftDeleteManager` (`deleted_at`) em `apps/common/models.py`; Organization/Service/Person/Resource/Role passam a `(SoftDeleteModel, ActiveModel)`; `Role` ganha `active`; 5 migrações novas | Feito    |
| 2026-09-06 | RBAC catálogo (v3) | `seed_rbac` ganha `*.create`/`*.delete`, `role.*`, `organization.manage`, `people.manage`; activate/deactivate gated por `*.manage`; SERVICE_MANAGER/OPERATIONS_MANAGER ganham `organization.view` | Feito    |
| 2026-09-06 | Lifecycle views    | `apps/common/lifecycle.py` (`CatalogCreateView`, `LifecycleActionView` POST-only); create/activate/deactivate/delete por app (organizations, services, people, resources, access) com `.alive()` nas listas/detalhes | Feito    |
| 2026-09-06 | Templates ciclo    | `templates/catalog/create.html` + `templates/catalog/actions.html`; botões "New X" e ações ligados em listas/detalhes; tag filtro `has_perm` em `apps/common/templatetags/ui.py` | Feito    |
| 2026-09-06 | Testes ciclo       | `tests/test_catalog_lifecycle.py`: create, 403, 405, 404 fora-de-escopo, activate/deactivate, soft-delete ocultado da UI (22 testes) | Feito    |
| 2026-09-07 | Perfis (v4)        | `apps.profiles`: `UserProfile` (OneToOne `auth.User`, campo `language`), `UserLanguageMiddleware` (ativa idioma guardado após Auth), `ProfileView` + `SettingsView` (FormView com identidade + idioma), URLs namespace `profiles:`; migração `0001_initial` | Feito    |
| 2026-09-07 | i18n settings      | `LANGUAGES=[en,pt]`, `LANGUAGE_CODE=en`, `LOCALE_PATHS`, `LocaleMiddleware`; `UserLanguageMiddleware` PÓS `AuthenticationMiddleware` (bug corrigido: ficava antes → `request.user` era `None`) | Feito    |
| 2026-09-07 | Wrapping strings   | Todas as strings visíveis em Python (breadcrumbs, `Column(...)`, captions/empty states, BulkAction, labels/help_texts, choices) e templates (`{% translate %}`; `blocktranslate` com `%(var)s`) | Feito    |
| 2026-09-07 | Catálogo PT        | `build_catalog.py` (polib) gera `locale/pt/LC_MESSAGES/django.{po,mo}` — 512 msgids, 0 untranslated; construído sem `msgfmt`/`xgettext`; `COPY . .` no Dockerfile dá `.mo` ao runtime | Feito    |
| 2026-09-07 | Testes i18n        | `tests/test_profiles_i18n.py`: redirect anónimo, render profile/settings, POST guarda identidade+idioma, middleware reativa idioma, persistência entre sessões, EN por defeito (9 testes) | Feito    |
| 2026-09-07 | Verificação live   | Docker rebuild + curl: login, `/settings/` POST `language=pt`, páginas PT (`Guardar alterações`, `Problemas`, `O meu perfil`), persistência após logout+login; seed fix: `root_cause=""` (era `None` → violava NOT NULL) | Feito    |

---

## Decisões de design (log)

1. **Monólito modular** — uma `config` project + 13 apps de domínio em `apps/`.
2. **PostgreSQL** como backend obrigatório; hierarquia de organizações com **ltree** (`path`) + `parent` FK para leitura conveniente.
3. **Sem circular dependency**: apps com acoplamento genérico (`events`, `audit`, workflows, resources) usam **GenericForeignKey** (contenttypes).
4. **Autorização centralizada**: `apps/common/authorization.py` com `can()` e managers `.visible_to(user)` que filtram ao nível da base de dados (nunca em Python).
5. **Workflow = definição**; **WorkflowExecution = instância**; versões imutáveis via `version` + `unique(service, name, version)`.
6. **Eventos imutáveis** (`editable=False`), prontos para audit/notifications/automação.
7. **Dashboards fora do modelo core** — são views sobre os dados (princípio §21 da spec).
8. **Campo ltree próprio** — o Django 5.2 não traz `LTreeField`; implementado em `apps/common/fields.py` com lookups `descendants`/`ancestors`/`nlevel`.
9. **Índice GiST** — o PostgreSQL 16 removeu o opclass ltree GIN; usamos `GistIndex(... opclasses=["gist_ltree_ops"])` sobre `path`.
10. **Migrações reais nos testes** — sem `--nomigrations` no pytest, para o `CreateExtension('ltree')` correr na BD de teste.
11. **ServiceAccess** — membership com linhas → restrita àqueles serviços; sem linhas → todos os serviços do sub-árvore da organização.
12. **Acesso flui para baixo** — herda para descendentes, nunca para ascendentes/irmãos; leitura e escrita são átomos distintos.

## Notas técnicas

- URL da BD (env): `POSTGRES_*` em `.env` → `orgplatform`@`127.0.0.1:5432`/`orgplatform` (container `orgplatform_db`).
- `python manage.py seed_rbac` — idempotente; cria **41 permissões + 9 roles** (v3 adicionou `*.create`/`*.delete`, `role.*`, `organization.manage`, `people.manage`).
- `python manage.py seed_default_data` — idempotente; chama `seed_rbac` + cria dataset default (superuser `admin`, demo users, org tree, serviços, workflows, requests/incidents/problems/changes, resources, metrics/KPI/SLA, events, audit). Demo users: `<username>/<username>123`.
- Soft-delete (v3): o manager default mantém a semântica `all()` — os registos eliminados continuam nos querysets da BD; a UI oculta-os explicitamente via `.alive()` nas listas/detalhes/ações (necessário para `get_or_create`/`update_or_create` dos seeds continuarem idempotentes). "Eliminar" = `obj.soft_delete()` (preenche `deleted_at`); nunca há hard delete.
- Ciclo de vida (v3): URLs por app — `services/new/`, `services/<pk>/activate|deactivate|delete/` (análogo p/ organizations, people, resources, roles). Criar exige `*.create`; ativar/desativar exige `*.manage`; eliminar exige `*.delete`. Ações são POST-only (405 em GET) e validadas por escopo da organização (404 fora do scope).
- Web em Docker: `docker compose up -d --build` sobe `db` + `web` (http://localhost:8000); o entrypoint corre `migrate` + seeds automaticamente (`SEED_DATA=0` desativa).
- Login página: `/login/` (namespace `dashboard:login`); superuser `admin` / `admin123` (env `DJANGO_SUPERUSER_*`).
- `python manage.py migrate --noinput` aplica tudo (incluindo `CreateExtension("ltree")` em `organizations.0001`).
- Bug resolvido durante o desenvolvimento: `run_from_argv` fecha todas as conexões no `finally` — os testes de seed usam `call_command`, não `run_from_argv`.
- Bug resolvido v2: `apex_field` não aceitava `**attrs` (login 500) — corrigido em `apps/common/templatetags/ui.py`.
- Bug resolvido v2: `RequestDetailView` usava `context_object_name` default (`request` mascara o HTTP request) → `context_object_name="req"`.
- Bug resolvido v3: `{% if has_perm request.user "code" %}` (simple_tag) não funciona em `{% if %}` — `has_perm` é um **filter**: `{% if request.user|has_perm:"code" %}`; forms com kwarg `user` aceitam-no em `__init__` (`user=None`) e o scope do picker de organização depende de `organization.view`.
- i18n (v4): sem `msgfmt`/`xgettext` no host — o catálogo PT é gerado por script (`polib`): `poentry.msgid` com placeholders `%(var)s` (sintaxe `blocktranslate` do Django) e `save_as_mofile()`. Rebuild: edit `T`/`PLURALS` no `build_catalog.py` → correr extractor + builder → `docker compose up -d --build web` (sem volume mount).
- i18n (v4) — gotchas: `{{ x|default:_("...") }}` é inválido → usar `{% if %}`/`{% else %}`; `{% translate %}` não pode estar dentro de args de `{% include with label="..." %}` → capturar com `{% translate '...' as var %}`; `blocktranslate` não aceita outros block tags (`{% url %}`) no corpo → manter markup fora da mensagem; lazy strings (`_()`) não são JSON-serializáveis → `str(name)` antes de `json.dumps` (charts do dashboard).
- i18n (v4): `LANGUAGE_SESSION_KEY` não existe nesta versão do Django (idioma vai em cookie `django_language`); a persistência é garantida pela BD (`UserProfile.language`), não pela sessão. Ordem de middleware: `UserLanguageMiddleware` tem de correr DEPOIS de `AuthenticationMiddleware`.

## Como correr

```bash
docker compose up -d --build        # Postgres 16 + Django web (http://localhost:8000)
# ou localmente:
docker compose up -d db
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_default_data   # RBAC + dataset default (opcional)
pytest
```

## Checklist final

- [x] Todos os apps com `models.py`, `__str__`, choices, indexes, constraints
- [x] Migrações aplicadas em Postgres
- [x] Camada de autorização central (`can()`, `.visible_to()`, `PermissionRequiredMixin`)
- [x] Views de listagem + detalhe + filtros + create para todos os 13 domínios
- [x] Dashboards (executivo, organização, serviço) com charts
- [x] Ciclo de vida do catálogo core (v3): create / activate / deactivate / soft-delete (Organization, Service, Person, Resource, Role)
- [x] Perfil & Definições (v4): `apps/profiles` + dropdown no header; guarda identidade e idioma
- [x] UI bilingue (v4): EN por defeito, PT com `locale/pt/LC_MESSAGES/django.{po,mo}` compilado (polib)
- [x] Testes verdes (`pytest` → 146 passed; `tests/test_profiles_i18n.py` 9 testes; `tests/test_service_roles_ui.py` 4 testes)
- [x] Docker web service funcional (Django em container + Postgres)
- [x] Dataset padrão idempotente (`seed_default_data`) + superuser + demo users
- [x] Docs + diagrama ER (`docs/`)
- [x] Papéis de serviço (v5): secção "Service roles" no perfil e seletor de serviços no dropdown do header (liga para o dashboard do serviço; destaca o serviço ativo via `active_service`); PROBLEM_MANAGER passou a ter `service.view`
- [x] Seletor de contexto serviço/papel (v5): tab "Select service / role" no dropdown do perfil — POST `dashboard:set_service_context` grava a escolha na sessão; chip do header mostra o serviço+papel ativo e persiste entre páginas
- [x] Seletor de organização de seleção única (v5): tab "Organization / Role" agora lista uma linha por organização (não por papel), mostrando o caminho completo da hierarquia (ex. "Portugal / Lisboa / Torres Vedras") em vez dos papéis; selecionável via `dashboard:set_org_context`. Tab "Service / Role" ("Serviço / Função") lista Serviço + Função; admin recebeu todos os papéis em Torres Vedras para testar a troca de contexto
- [x] Fix (v5): cartões dos dashboards (executivo/organização/serviço) deixaram de abrir listas sem filtro — cada "drill down" (Open Requests, Active Incidents, Open Problems, Workflows, Executions) agora passa `?service=<pk>` (exato, via novo `TableView.service_filter_field`) e o `status` correspondente, incluindo o pseudo-estado `status=OPEN` para Requests (que não tem um único valor "aberto" no enum)
- [x] UI (v5): filtro "Service" nas tabelas (Requests, Incidents, Problems, Workflows, Workflow Executions) passou de campo de texto livre (`icontains` no nome) para dropdown (`<select>`) com os serviços visíveis ao utilizador — `Filter.choices_callable` + `Filter.lookup` (novo, em `apps/common/tables/config.py`) permite filtrar por `service_id` exato em vez de substring do nome

## Próximos passos sugeridos (fora do âmbito atual)

- **Editar / detalhar catálogo** — ecrã de update para entidades core e extensão do ciclo de vida a requests/incidents/changes
- Forms AJAX / HTMX para ações in-line (ativar/desativar, mudar status)
- `admin.py` registrations para todos os apps (Django admin como back-office)
- API REST (DRF ou django-ninja) para integrações externas
- Motor de execução de workflows (transições entre steps com validação)
- Emissão de eventos/audit a partir dos `save()` / actions das views
- CI/CD (GitHub Actions) — lint + testes + Docker build
- Perfis de utilizador extras (SCIM/LDAP para integração SSO)
- i18n (v4): validar/rever a tradução PT com falante nativo; adicionar mais idiomas ao catálogo; gerar catálogo no CI com `django-admin makemessages/compilemessages` quando houver `gettext` instalado