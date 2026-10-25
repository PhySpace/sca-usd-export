"""Real packages exercise persistence, per-part mapping and tamper detection."""

from copy import deepcopy

import pytest
import simplecadapi as scad
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from sca_physics.contract import inventory
from sca_physics.package_io import enrich_package
from sca_usd_export.config import ExportConfig
from sca_usd_export.exporter import export_package
from sca_usd_export.validation import validate_usd


@pytest.fixture(scope="module")
def physical_package():
    @scad.part(id="physics-test-base")
    def base():
        part = scad.make_part_rpart("physics-test-base", scad.make_box_rsolid(width=20, height=20, depth=20))
        part = scad.assign_material_rpart(part, scad.make_material_rmaterial("base-steel", density=7800, density_unit="kg/m^3"))
        return scad.add_connector_rpart(part, scad.make_placement_connector_rconnector("axis", scad.identity_placement_rplacement()))

    @scad.part(id="physics-test-arm")
    def arm():
        part = scad.make_part_rpart("physics-test-arm", scad.make_box_rsolid(width=100, height=10, depth=10))
        part = scad.assign_material_rpart(part, scad.make_material_rmaterial("arm-alloy", density=2700, density_unit="kg/m^3"))
        return scad.add_connector_rpart(part, scad.make_placement_connector_rconnector("axis", scad.identity_placement_rplacement()))

    first, second = base(), arm()

    @scad.assemble(id="physics-test-root", definitions=(first, second))
    def assembly():
        item = scad.make_assembly_rassembly("physics-test-root")
        item = scad.add_component_rassembly(item, first.value, "base", scad.identity_placement_rplacement())
        item = scad.add_component_rassembly(item, second.value, "arm", scad.identity_placement_rplacement())
        item = scad.ground_component_rassembly(item, "base")
        return scad.add_revolute_constraint_rassembly(item, "hinge",
            scad.make_connector_ref_rconnectorref("base", "axis"),
            scad.make_connector_ref_rconnectorref("arm", "axis"))

    raw = scad.build_product_package(assembly())
    ids = inventory(raw)
    base_id = next(key for key in ids["parts"] if key.endswith("/base"))
    arm_id = next(key for key in ids["parts"] if key.endswith("/arm"))
    joint_id = next(iter(ids["joints"]))
    config = {
        "source": {"kind": "estimated", "detail": "Synthetic regression choices for contact and passive joint behavior; not user measurements"},
        "parts": {
            base_id: {"contact": {"static_friction": 0.8, "dynamic_friction": 0.6, "restitution": 0.25}},
            arm_id: {"contact": {"static_friction": 0, "dynamic_friction": 0.1, "restitution": 0.05}},
        },
        "joints": {joint_id: {"damping": 0.0008, "collision_enabled": False}},
        "assembly": {"collision_exclusions": [{"a": base_id, "b": arm_id,
            "source": {"kind": "explicit", "detail": "test explicit instance exclusion"}}]},
    }
    package, preservation = enrich_package(raw, config)
    assert preservation["geometry_material_history_blobs_preserved"]
    return package, base_id, arm_id, joint_id


def test_embedded_package_export_needs_no_external_json(tmp_path, physical_package):
    package, base_id, arm_id, joint_id = physical_package
    source = tmp_path / "final.scadpkg"
    source.write_bytes(scad.encode_product_package(package))
    report = export_package(source, tmp_path / "final.usda")
    assert report["physics"]["input_configuration"]["origin"].endswith("metadata.sca_physics")
    assert report["configuration"]["up_axis"] == "Z"
    assert report["physics"]["dynamic_body_count"] == 1
    values = report["validation"]["physics_values"]
    assert values["contact_parts_checked"] == 2
    assert values["passive_joints_checked"] == 1
    assert values["collision_exclusions_checked"] == 1
    settings = report["physics"]["input_configuration"]["resolved"]
    assert settings["parts"][arm_id]["contact"]["static_friction"]["value"] == 0
    assert report["physics"]["joints"][0]["passive_drive"]["damping"] == pytest.approx(0.0008 * 3.141592653589793 / 180)


def test_collision_false_is_preserved(tmp_path, physical_package):
    from dataclasses import replace
    package, base_id, arm_id, joint_id = physical_package
    metadata = deepcopy(dict(package.root_definition.metadata))
    metadata["sca_physics"]["parts"][arm_id]["collision"]["enabled"]["value"] = False
    definition = replace(package.root_definition, metadata=metadata, content_hash="")
    modified = scad.build_product_package(definition)
    report = export_package(modified, tmp_path / "disabled.usda")
    part = next(p for group in report["physics"]["groups"] for p in group["parts"] if p["node_id"] == arm_id)
    assert part["collision_enabled"] is False
    assert report["validation"]["ok"]


def test_embedded_physics_overrides_require_explicit_direction(tmp_path, physical_package):
    package = physical_package[0]
    with pytest.raises(ValueError, match="Embedded physics takes precedence"):
        export_package(package, tmp_path / "forbidden.usda", config=ExportConfig(default_friction=0.9))
    report = export_package(package, tmp_path / "override.usda", up_axis_override="Y")
    change = report["physics"]["input_configuration"]["overrides"][0]
    assert change["before"]["value"] == "+Z"
    assert change["after"]["value"] == "+Y"
    assert report["validation"]["up_axis"] == "Y"


@pytest.mark.parametrize("tamper", ["static_friction", "dynamic_friction", "restitution", "damping",
    "collision_enabled", "approximation", "up_axis", "gravity", "joint_body", "anchor", "axis",
    "joint_rotation", "joint_collision", "filter", "active_target"])
def test_tampered_physics_is_detected(tmp_path, physical_package, tamper):
    output = tmp_path / (tamper + ".usda")
    report = export_package(physical_package[0], output)
    stage = Usd.Stage.Open(str(output))
    group = next(group for group in report["physics"]["groups"] if group["dynamic"])
    part = group["parts"][0]
    shape = stage.GetPrimAtPath(part["shape_path"])
    joint_record = next(j for j in report["physics"]["joints"] if j["authored"])
    joint = UsdPhysics.RevoluteJoint.Get(stage, joint_record["prim_path"])
    if tamper in {"static_friction", "dynamic_friction", "restitution"}:
        material = UsdPhysics.MaterialAPI(stage.GetPrimAtPath(part["contact_material_path"]))
        getter = {"static_friction": "GetStaticFrictionAttr", "dynamic_friction": "GetDynamicFrictionAttr", "restitution": "GetRestitutionAttr"}[tamper]
        getattr(material, getter)().Set(0.99)
    elif tamper == "damping":
        UsdPhysics.DriveAPI(joint.GetPrim(), "angular").GetDampingAttr().Set(1)
    elif tamper == "active_target":
        UsdPhysics.DriveAPI(joint.GetPrim(), "angular").GetTargetVelocityAttr().Set(30)
    elif tamper == "collision_enabled":
        UsdPhysics.CollisionAPI(shape).GetCollisionEnabledAttr().Set(False)
    elif tamper == "approximation":
        UsdPhysics.MeshCollisionAPI(shape).GetApproximationAttr().Set("boundingCube")
    elif tamper == "up_axis":
        UsdGeom.SetStageUpAxis(stage, "Y")
    elif tamper == "gravity":
        UsdPhysics.Scene.Get(stage, report["physics"]["scene"]["prim_path"]).GetGravityDirectionAttr().Set(Gf.Vec3f(1, 0, 0))
    elif tamper == "joint_body":
        joint.GetBody1Rel().SetTargets([Sdf.Path(joint_record["body0_path"])])
    elif tamper == "anchor":
        joint.GetLocalPos0Attr().Set(Gf.Vec3f(1, 0, 0))
    elif tamper == "axis":
        joint.GetAxisAttr().Set("X")
    elif tamper == "joint_rotation":
        joint.GetLocalRot1Attr().Set(Gf.Quatf(0, Gf.Vec3f(1, 0, 0)))
    elif tamper == "joint_collision":
        joint.GetCollisionEnabledAttr().Set(True)
    else:
        pair = report["physics"]["collision_exclusions"][0]
        stage.GetPrimAtPath(pair["a_path"]).GetRelationship("physics:filteredPairs").ClearTargets(True)
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError):
        validate_usd(output)
