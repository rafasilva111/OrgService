"""Resources: multi-table inheritance (Asset/Equipment/Material <- Resource)."""

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.resources.models import (
    Asset,
    Equipment,
    Material,
    Resource,
    ResourceAssignment,
)


@pytest.fixture
def resource_graph(db, org_tree, make_service):
    service = make_service(org_tree["parish_a"])
    asset = Asset.objects.create(
        organization=org_tree["parish_a"],
        code="AST-1",
        name="Fire hydrant",
        resource_type="ASSET",
        serial_number="SN-001",
    )
    equip = Equipment.objects.create(
        organization=org_tree["parish_a"],
        code="EQP-1",
        name="Excavator",
        resource_type="EQUIPMENT",
        equipment_type="excavator",
        capacity="10t",
    )
    material = Material.objects.create(
        organization=org_tree["parish_a"],
        code="MAT-1",
        name="Cement",
        resource_type="MATERIAL",
        unit="bag",
        quantity_on_hand=120,
    )
    return {"service": service, "asset": asset, "equip": equip, "material": material}


@pytest.mark.django_db
def test_multi_table_inheritance(resource_graph):
    g = resource_graph
    assert g["equip"].organization_id == g["asset"].organization_id
    assert g["equip"].equipment_type == "excavator"
    assert g["equip"].capacity == "10t"
    assert g["material"].quantity_on_hand == 120
    # The base table contains every subtype.
    assert Resource.objects.count() == 3
    base = Resource.objects.get(pk=g["equip"].pk)
    assert base.asset.pk == g["equip"].pk
    assert base.asset.equipment.pk == g["equip"].pk


@pytest.mark.django_db
def test_asset_defaults(resource_graph):
    asset = Asset.objects.create(
        organization=resource_graph["asset"].organization,
        name="Truck",
        resource_type="ASSET",
    )
    assert asset.status == "AVAILABLE"
    assert asset.code is None


@pytest.mark.django_db
def test_resource_assignment_generic_target(
    resource_graph, make_person, make_role, make_membership
):
    person = make_person()
    role = make_role()
    membership = make_membership(person, resource_graph["asset"].organization, role)
    assignment = ResourceAssignment.objects.create(
        resource=resource_graph["equip"],
        target_type=ContentType.objects.get_for_model(membership),
        target_id=membership.pk,
        label="driver",
    )
    assert assignment.target == membership
    assert assignment.label == "driver"
    assert resource_graph["equip"].assignments.count() == 1

    assignment.ended_at = None
    assignment.save()
    assert ResourceAssignment.objects.get(pk=assignment.pk).label == "driver"
