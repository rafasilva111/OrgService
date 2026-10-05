"""ServiceType model + Service.service_type + Service.parent + ServiceConsumption."""

from django.db import migrations, models
import django.db.models.deletion
import apps.services.models


def create_service_types(apps, schema_editor):
    ServiceType = apps.get_model("services", "ServiceType")
    Service = apps.get_model("services", "Service")
    db_alias = schema_editor.connection.alias

    types = {
        "finance": ("FINANCE", "ORGANIZATIONS_AND_PEOPLE"),
        "hr": ("HR", "ORGANIZATIONS_AND_PEOPLE"),
        "supplies": ("SUPPLIES", "PARTNERS_AND_SUPPLIERS"),
        "it": ("IT", "INFORMATION_AND_TECHNOLOGY"),
        "processes": ("PROCESSES", "VALUE_STREAMS_AND_PROCESSES"),
        "management": ("MANAGEMENT", "ORGANIZATIONS_AND_PEOPLE"),
    }
    created = {}
    for code, (type_code, dimension) in types.items():
        obj, _ = ServiceType.objects.using(db_alias).get_or_create(
            code=code,
            defaults={"name": {"FINANCE": "Finance", "HR": "Human Resources", "SUPPLIES": "Supplies", "IT": "IT", "PROCESSES": "Processes", "MANAGEMENT": "Management"}[type_code], "dimension": dimension},
        )
        created[code] = obj

    # Backfill existing services: assign a sensible default type based on name.
    name_to_type = {
        "Municipal Administration": "management",
        "Water Supply": "processes",
        "Sanitation Network": "processes",
        "Waste Management": "processes",
        "Road Maintenance": "processes",
        "Public Lighting": "processes",
        "Public Health": "hr",
        "Urban Cleaning": "processes",
        "Green Spaces & Parks": "processes",
    }
    for svc in Service.objects.using(db_alias).all():
        type_code = name_to_type.get(svc.name)
        if type_code and type_code in created:
            svc.service_type = created[type_code]
            svc.save(update_fields=["service_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("services", "0002_service_deleted_at"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.SlugField(max_length=64, unique=True, verbose_name="code")),
                ("name", models.CharField(max_length=128, verbose_name="name")),
                (
                    "dimension",
                    models.CharField(
                        choices=[
                            ("ORGANIZATIONS_AND_PEOPLE", "Organizations and People"),
                            ("INFORMATION_AND_TECHNOLOGY", "Information and Technology"),
                            ("PARTNERS_AND_SUPPLIERS", "Partners and Suppliers"),
                            ("VALUE_STREAMS_AND_PROCESSES", "Value Streams and Processes"),
                        ],
                        max_length=64,
                        verbose_name="dimension",
                    ),
                ),
            ],
            options={"verbose_name": "service type", "verbose_name_plural": "service types"},
        ),
        migrations.AddField(
            model_name="service",
            name="service_type",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="services",
                to="services.servicetype",
                verbose_name="service type",
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="services.service",
                help_text="Parent service if this is a department/child service.",
            ),
        ),
        migrations.CreateModel(
            name="ServiceConsumption",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reason", models.TextField(blank=True, default="", verbose_name="reason")),
                ("since", models.DateField(blank=True, null=True, verbose_name="since")),
                (
                    "consumed",
                    models.ForeignKey(
                        help_text="The service being consumed.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="consumed_by",
                        to="services.service",
                    ),
                ),
                (
                    "service",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="consumed_through",
                        to="services.service",
                    ),
                ),
            ],
            options={"verbose_name": "service consumption", "verbose_name_plural": "service consumptions"},
        ),
        migrations.RunPython(create_service_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="service",
            name="service_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="services",
                to="services.servicetype",
                verbose_name="service type",
            ),
        ),
        migrations.AddConstraint(
            model_name="serviceconsumption",
            constraint=models.UniqueConstraint(fields=("service", "consumed"), name="u_service_consumption"),
        ),
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["service_type"], name="services_service_type_idx"),
        ),
    ]
