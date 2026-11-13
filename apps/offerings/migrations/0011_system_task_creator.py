from django.db import migrations

SYSTEM_PERSON_EMAIL = "system@localhost.invalid"


def attribute_task_creation_to_system(apps, schema_editor):
    Person = apps.get_model("people", "Person")
    TaskActivity = apps.get_model("offerings", "TaskActivity")
    person, _created = Person.objects.get_or_create(
        email=SYSTEM_PERSON_EMAIL,
        defaults={"full_name": "Sistema", "person_type": "SYSTEM"},
    )
    if person.person_type != "SYSTEM" or person.user_id is not None:
        raise RuntimeError(f"{SYSTEM_PERSON_EMAIL} is reserved for the non-login system person.")
    TaskActivity.objects.filter(action="created").exclude(actor_id=person.pk).update(
        actor_id=person.pk
    )


def clear_system_task_creator(apps, schema_editor):
    Person = apps.get_model("people", "Person")
    TaskActivity = apps.get_model("offerings", "TaskActivity")
    person = Person.objects.filter(email=SYSTEM_PERSON_EMAIL, person_type="SYSTEM").first()
    if person is None:
        return
    TaskActivity.objects.filter(action="created", actor_id=person.pk).update(actor_id=None)
    person.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("offerings", "0010_executionnote"),
        ("people", "0003_alter_person_person_type"),
    ]

    operations = [
        migrations.RunPython(attribute_task_creation_to_system, clear_system_task_creator),
    ]
