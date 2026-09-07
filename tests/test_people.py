"""People: persons, skills and person skills."""

import pytest
from django.db import IntegrityError, transaction

from apps.people.models import Person, PersonSkill, Skill


@pytest.mark.django_db
def test_person_creation_without_user():
    person = Person.objects.create(full_name="Jane Doe", person_type="EMPLOYEE")
    assert person.user is None
    assert str(person) == "Jane Doe"
    assert person.active is True


@pytest.mark.django_db
def test_person_links_to_user(make_user):
    user = make_user("jane")
    person = Person.objects.create(user=user, full_name="Jane Doe")
    person.refresh_from_db()
    assert person.user_id == user.pk


@pytest.mark.django_db
def test_email_unique_when_set():
    Person.objects.create(full_name="A", email="a@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        Person.objects.create(full_name="B", email="a@example.com")


@pytest.mark.django_db
def test_skill_and_person_skill_unique(make_person):
    skill = Skill.objects.create(code="plumbing", name="Plumbing")
    person = make_person()
    PersonSkill.objects.create(person=person, skill=skill, level="EXPERT")
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonSkill.objects.create(person=person, skill=skill)


@pytest.mark.django_db
def test_person_skill_level_and_verification(make_person):
    skill = Skill.objects.create(code="welding", name="Welding")
    person = make_person()
    verifier = make_person(name="Verifier")
    ps = PersonSkill.objects.create(
        person=person, skill=skill, level="ADVANCED", verified_by=verifier
    )
    assert ps.level == "ADVANCED"
    assert ps.verified_by == verifier
    assert list(person.skills.all()) == [ps]
