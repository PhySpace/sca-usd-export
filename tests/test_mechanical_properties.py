"""Small 1.1 extension: real USD/BREP, fixed groups and persisted occurrences."""
from copy import deepcopy

import pytest
from pxr import Sdf, Usd, UsdPhysics, UsdShade

from sca_usd_export.config import ExportConfig
from sca_usd_export.embedded_physics import legacy_settings
from sca_usd_export.physics import apply_physics
from sca_usd_export.physics_validation import validate_physics
from sca_usd_export.mechanical_properties import applied_schema_tokens
from test_physics import _fixture, _joint


def parameter(value, unit="1/s"):
    return {"value": value, "unit": unit,
            "source": {"kind": "explicit", "detail": "1.1 test input"}}


def engine_default(unit="1/s"):
    return {"mode": "engine_default", "unit": unit,
            "source": {"kind": "explicit", "detail": "Use backend default"}}


def settings_for(records, graph):
    return legacy_settings(records, graph, ExportConfig())


@pytest.mark.parametrize("mode,counts", [("static", (0, 0, 1)), ("dynamic", (1, 0, 0)), ("kinematic", (0, 1, 0))])
def test_body_modes_are_distinct_and_reopened(tmp_path, mode, counts):
    stage, records, graph = _fixture(("free",))
    settings = settings_for(records, graph)
    settings["parts"][records[-1]["node_id"]]["rigid_body"] = {"mode": parameter(mode, "token")}
    report = apply_physics(stage, records, graph, ExportConfig(), settings)
    assert tuple(report[key] for key in ("dynamic_body_count", "kinematic_body_count", "static_group_count")) == counts
    assert report["body_count"] == sum(counts[:2])
    path = tmp_path / (mode + ".usda")
    stage.GetRootLayer().Export(str(path))
    reopened = Usd.Stage.Open(str(path))
    validate_physics(reopened, report, tolerance=1e-7)
    group = report["groups"][0]
    assert group["mode"] == mode
    assert group["dynamic"] == (mode == "dynamic")
    assert group["kinematic"] == (mode == "kinematic")


@pytest.mark.parametrize("mode", ["dynamic", "kinematic"])
def test_native_ground_cannot_be_overridden(mode):
    stage, records, graph = _fixture(("base",))
    graph.ground_edges.append({"parent_node_id": records[0]["node_id"], "child_node_id": records[-1]["node_id"]})
    settings = settings_for(records, graph)
    settings["parts"][records[-1]["node_id"]]["rigid_body"] = {"mode": parameter(mode, "token")}
    with pytest.raises(ValueError, match="grounded or frozen"):
        apply_physics(stage, records, graph, ExportConfig(), settings)


@pytest.mark.parametrize("field,value,unit", [("gravity_enabled", False, "boolean"), ("linear_damping", .1, "1/s"), ("angular_damping", .2, "1/s")])
def test_static_explicit_body_parameters_fail_but_engine_default_is_noop(field, value, unit):
    stage, records, graph = _fixture(("static",))
    settings = settings_for(records, graph)
    rigid = {"mode": parameter("static", "token"), field: parameter(value, unit)}
    settings["parts"][records[-1]["node_id"]]["rigid_body"] = rigid
    with pytest.raises(ValueError, match="explicit gravity/damping"):
        apply_physics(stage, records, graph, ExportConfig(), settings)
    rigid[field] = engine_default(unit)
    report = apply_physics(stage, records, graph, ExportConfig(), settings)
    record = next(item for item in report["optional_properties"] if item["field"] == field)
    assert record["status"] == "not_applicable"
    assert not record["usd_authored"]
    assert "PhysxRigidBodyAPI" not in applied_schema_tokens(stage.GetPrimAtPath(record["prim_path"]))
    validate_physics(stage, report, tolerance=1e-7)


@pytest.mark.parametrize("field,first,second,unit", [("mode", "dynamic", "kinematic", "token"),
    ("gravity_enabled", True, False, "boolean"), ("linear_damping", .1, .2, "1/s"),
    ("angular_damping", .1, .2, "1/s")])
def test_fixed_group_conflicts_are_diagnosed(field, first, second, unit):
    stage, records, graph = _fixture(("left", "right"))
    _joint(graph, "fixed", "left", "right")
    settings = settings_for(records, graph)
    for record, value in zip(records[1:], (first, second)):
        settings["parts"][record["node_id"]]["rigid_body"] = {field: parameter(value, unit)}
    with pytest.raises(ValueError, match="conflicting rigid_body"):
        apply_physics(stage, records, graph, ExportConfig(), settings)


def test_fixed_group_agrees_and_records_both_sources():
    stage, records, graph = _fixture(("left", "right"))
    _joint(graph, "fixed", "left", "right")
    settings = settings_for(records, graph)
    for record in records[1:]:
        settings["parts"][record["node_id"]]["rigid_body"] = {
            "mode": parameter("dynamic", "token"), "linear_damping": parameter(.15)}
    report = apply_physics(stage, records, graph, ExportConfig(), settings)
    assert report["body_count"] == 1
    group = report["groups"][0]
    assert set(group["rigid_body_sources"]["linear_damping"]) == {part["node_id"] for part in records[1:]}
    validate_physics(stage, report, tolerance=1e-7)


@pytest.fixture(scope="module")
def shared_package():
    import simplecadapi as scad
    from sca_physics.contract import inventory

    @scad.part(id="mechanical-shared-block")
    def block():
        part = scad.make_part_rpart("mechanical-shared-block", scad.make_box_rsolid(width=10, height=10, depth=10))
        return scad.assign_material_rpart(part, scad.make_material_rmaterial("same-native-alloy", density=2700, density_unit="kg/m^3"))

    definition = block()

    @scad.assemble(id="mechanical-shared-root", definitions=(definition,))
    def assembly():
        result = scad.make_assembly_rassembly("mechanical-shared-root")
        result = scad.add_component_rassembly(result, definition.value, "left", scad.identity_placement_rplacement())
        return scad.add_component_rassembly(result, definition.value, "right", scad.identity_placement_rplacement())

    package = scad.build_product_package(assembly())
    ids = inventory(package)["parts"]
    return package, {key.rsplit("/", 1)[-1]: key for key in ids}


def test_shared_native_definition_has_independent_bound_contact_materials(tmp_path, shared_package):
    import simplecadapi as scad
    from sca_physics.package_io import enrich_package
    from sca_usd_export.exporter import export_package
    from sca_usd_export.validation import validate_usd

    raw, ids = shared_package
    package, _ = enrich_package(raw, {"source": {"kind": "estimated", "detail": "Synthetic regression choices, not calibrated or user supplied"}, "parts": {
        ids["left"]: {"rigid_body": {"mode": "dynamic", "gravity_enabled": False,
            "linear_damping": .12, "angular_damping": .23},
            "collision": {"contact_offset": .0001, "rest_offset": -.00002},
            "contact": {"static_friction": .82, "dynamic_friction": .61,
                "friction_combine_mode": "max", "restitution_combine_mode": "min"}},
        ids["right"]: {"rigid_body": {"mode": "kinematic", "linear_damping": .34},
            "contact": {"static_friction": .17, "dynamic_friction": .14,
                "friction_combine_mode": "multiply", "restitution_combine_mode": "average"}},
    }})
    source = tmp_path / "shared.scadpkg"
    source.write_bytes(scad.encode_product_package(package))
    report = export_package(source, tmp_path / "shared.usda")
    assert report["physics"]["input_configuration"]["resolved"]["schema_version"] == "1.7"
    assert report["physics"]["dynamic_body_count"] == 1
    assert report["physics"]["kinematic_body_count"] == 1
    assert report["physics"]["static_group_count"] == 0
    groups = {group["parts"][0]["node_id"]: group for group in report["physics"]["groups"]}
    stage = Usd.Stage.Open(str(tmp_path / "shared.usda"))
    paths = []
    for name, friction, combine in (("left", .82, "max"), ("right", .17, "multiply")):
        part = groups[ids[name]]["parts"][0]
        shape = stage.GetPrimAtPath(part["shape_path"])
        material, _ = UsdShade.MaterialBindingAPI(shape).ComputeBoundMaterial(materialPurpose="physics")
        assert str(material.GetPath()) == part["contact_material_path"]
        assert UsdPhysics.MaterialAPI(material.GetPrim()).GetStaticFrictionAttr().Get() == pytest.approx(friction)
        assert material.GetPrim().GetAttribute("physxMaterial:frictionCombineMode").Get() == combine
        paths.append(str(material.GetPath()))
    assert paths[0] != paths[1]
    assert len({group["parts"][0]["definition_id"] for group in groups.values()}) == 1
    assert validate_usd(tmp_path / "shared.usda")["ok"]
    kinematic = [item for item in report["physics"]["optional_properties"] if item["node_ids"] == [ids["right"]] and item["field"] == "linear_damping"][0]
    assert kinematic["applicability"] == "no_dynamic_integration_in_kinematic_mode"


@pytest.mark.parametrize("state", ["omitted", "engine_default", "authored"])
def test_optional_state_and_value_tampering(tmp_path, state):
    stage, records, graph = _fixture(("free",))
    settings = settings_for(records, graph)
    part = settings["parts"][records[-1]["node_id"]]
    part["rigid_body"] = {"mode": parameter("dynamic", "token")}
    fields = [(part["rigid_body"], "gravity_enabled", False, "boolean"),
              (part["rigid_body"], "linear_damping", .12, "1/s"),
              (part["rigid_body"], "angular_damping", .23, "1/s"),
              (part["collision"], "contact_offset", .0001, "m"),
              (part["collision"], "rest_offset", -.00002, "m"),
              (part["contact"], "friction_combine_mode", "max", "token"),
              (part["contact"], "restitution_combine_mode", "min", "token")]
    for section, key, value, unit in fields:
        if state != "omitted":
            section[key] = engine_default(unit) if state == "engine_default" else parameter(value, unit)
    report = apply_physics(stage, records, graph, ExportConfig(), settings)
    assert {record["status"] for record in report["optional_properties"]} == {state}
    path = tmp_path / (state + ".usda")
    stage.GetRootLayer().Export(str(path))
    stage = Usd.Stage.Open(str(path))
    validate_physics(stage, report, tolerance=1e-7)
    for record in report["optional_properties"]:
        prim = stage.GetPrimAtPath(record["prim_path"])
        previous = stage.GetRootLayer().ExportToString()
        if record["field"] == "gravity_enabled":
            attribute = prim.CreateAttribute(record["usd_attribute"], Sdf.ValueTypeNames.Bool, custom=False)
            attribute.Set(False if state == "authored" else True)
        elif "combine_mode" in record["field"]:
            prim.CreateAttribute(record["usd_attribute"], Sdf.ValueTypeNames.Token, custom=False).Set("multiply")
        else:
            prim.CreateAttribute(record["usd_attribute"], Sdf.ValueTypeNames.Float, custom=False).Set(9.0)
        with pytest.raises(ValueError, match="USD optional"):
            validate_physics(stage, report, tolerance=1e-7)
        stage.GetRootLayer().ImportFromString(previous)
        if state != "omitted":
            prim = stage.GetPrimAtPath(record["prim_path"])
            prim.RemoveAppliedSchema(record["api"])
            with pytest.raises(ValueError, match="USD optional physics API missing"):
                validate_physics(stage, report, tolerance=1e-7)
            stage.GetRootLayer().ImportFromString(previous)
    validate_physics(stage, report, tolerance=1e-7)
