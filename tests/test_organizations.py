"""Organizations: hierarchy (ltree), memberships, units."""

import pytest
from django.db import IntegrityError, transaction

from apps.organizations.models import Organization, OrganizationUnit


@pytest.mark.django_db
def test_ltree_path_is_built_from_parents(org_tree):
    t = org_tree
    assert t["district"].path == "dist"
    assert t["municipality"].path == "dist.muni"
    assert t["parish_a"].path == "dist.muni.pa"
    assert t["parish_b"].path == "dist.muni.pb"


@pytest.mark.django_db
def test_descendants_and_ancestors_queries(org_tree):
    t = org_tree
    municipality = t["municipality"]
    parish_a = t["parish_a"]

    desc = municipality.get_descendants()
    assert {o.code for o in desc} == {"pa", "pb"}

    desc_self = municipality.get_descendants(include_self=True)
    assert {o.code for o in desc_self} == {"muni", "pa", "pb"}

    anc = parish_a.get_ancestors()
    assert {o.code for o in anc} == {"dist", "muni"}

    anc_self = parish_a.get_ancestors(include_self=True)
    assert {o.code for o in anc_self} == {"dist", "muni", "pa"}

    # ltree depth lookup exposed by the custom field.
    assert Organization.objects.filter(path__nlevel=2).count() == 1  # muni


@pytest.mark.django_db
def test_code_unique_among_siblings(org_tree):
    muni = org_tree["municipality"]
    Organization.objects.create(name="Parish C", code="pc", organization_type="PARISH", parent=muni)
    with pytest.raises(IntegrityError), transaction.atomic():
        Organization.objects.create(
            name="Parish C2", code="pc", organization_type="PARISH", parent=muni
        )


@pytest.mark.django_db
def test_same_code_allowed_in_different_branches(org_tree):
    t = org_tree
    other = Organization.objects.create(
        name="Other",
        code="ot",
        organization_type="MUNICIPALITY",
        parent=t["district"],
    )
    # Same code 'pa' under a different parent is allowed.
    Organization.objects.create(name="Parish", code="pa", organization_type="PARISH", parent=other)
    assert Organization.objects.filter(code="pa").count() == 2
    assert other.path == "dist.ot"


@pytest.mark.django_db
def test_membership_uniqueness(org_tree, make_person, make_role, make_membership):
    person = make_person()
    role = make_role()
    make_membership(person, org_tree["municipality"], role)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_membership(person, org_tree["municipality"], role)


@pytest.mark.django_db
def test_unit_nested_path(org_tree):
    muni = org_tree["municipality"]
    ops = OrganizationUnit.objects.create(organization=muni, name="Ops", code="ops")
    fleet = OrganizationUnit.objects.create(
        organization=muni, name="Fleet", code="fleet", parent=ops
    )
    assert ops.path == "muni.ops"
    assert fleet.path == "muni.ops.fleet"


@pytest.mark.django_db
def test_str_representation(org_tree):
    assert str(org_tree["parish_a"]) == "dist.muni.pa"
