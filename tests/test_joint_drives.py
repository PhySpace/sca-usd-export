"""Saved ordinary-joint actuators map to real USD fields in metre/cm stages."""
from copy import deepcopy
import math
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import simplecadapi as scad
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from sca_physics.contract import inventory
from sca_physics.package_io import enrich_package
from sca_usd_export import update_drive_targets
from sca_usd_export.config import ExportConfig
from sca_usd_export.exporter import export_package
from sca_usd_export.validation import validate_usd


def sourced(value, unit):
    return {"value": value, "unit": unit, "source": {"kind": "estimated",
        "detail": "Synthetic regression actuator setting; not a measured motor specification"}}


def actuator(kind, mode="position"):
    angular = kind == "revolute"
    return {key: sourced(value, unit) for key, value, unit in (
        ("mode", mode, "token"), ("type", "force", "token"),
        ("target_position", .15 if angular else .004, "rad" if angular else "m"),
        ("target_velocity", .2 if angular else .003, "rad/s" if angular else "m/s"),
        ("stiffness", 2 if mode == "position" else 0, "N*m/rad" if angular else "N/m"),
        ("damping", .03, "N*m*s/rad" if angular else "N*s/m"),
        ("max_force", .4, "N*m" if angular else "N"))}


@pytest.fixture(scope="module", params=["revolute", "prismatic"])
def driven_package(request):
    kind = request.param
    @scad.part(id="actuator-base-" + kind)
    def base():
        p = scad.make_part_rpart("actuator-base-" + kind, scad.make_box_rsolid(width=10, height=10, depth=10))
        return scad.add_connector_rpart(p, scad.make_placement_connector_rconnector("axis", scad.identity_placement_rplacement()))
    @scad.part(id="actuator-link-" + kind)
    def link():
        p = scad.make_part_rpart("actuator-link-" + kind, scad.make_box_rsolid(width=10, height=10, depth=10))
        return scad.add_connector_rpart(p, scad.make_placement_connector_rconnector("axis", scad.identity_placement_rplacement()))
    first, second = base(), link()
    @scad.assemble(id="actuator-" + kind, definitions=(first, second))
    def assembly():
        a = scad.make_assembly_rassembly("actuator-" + kind)
        a = scad.add_component_rassembly(a, first.value, "base", scad.identity_placement_rplacement())
        a = scad.add_component_rassembly(a, second.value, "link", scad.identity_placement_rplacement())
        a = scad.ground_component_rassembly(a, "base")
        fn = scad.add_revolute_constraint_rassembly if kind == "revolute" else scad.add_prismatic_constraint_rassembly
        kwargs = {"drive_angle_degrees": 23} if kind == "revolute" else {"drive_distance": 3}
        return fn(a, "drive", scad.make_connector_ref_rconnectorref("base", "axis"),
                  scad.make_connector_ref_rconnectorref("link", "axis"), **kwargs)
    package = scad.build_product_package(assembly())
    ids = inventory(package)
    jid = next(iter(ids["joints"]))
    limit_unit = "rad" if kind == "revolute" else "m"
    config = {"joints": {jid: {"damping": sourced(.01, "N*m*s/rad" if kind == "revolute" else "N*s/m"),
        "motion": sourced("limited", "token"), "lower_limit": sourced(-1 if kind == "revolute" else -.01, limit_unit),
        "upper_limit": sourced(1 if kind == "revolute" else .01, limit_unit),
        "max_velocity": sourced(.5, "rad/s" if kind == "revolute" else "m/s"),
        "friction": sourced(.05, "1"), "drive": actuator(kind)}},
        "assembly": {"articulation": {"enabled": sourced(True, "boolean"),
            "root_node_id": ids["root_node_id"], "fixed_base": sourced(True, "boolean")}}}
    return package, config, kind, jid


@pytest.mark.parametrize("mpu", [1, .01])
@pytest.mark.parametrize("mode", ["position", "velocity", "off"])
def test_saved_drive_mapping_units_and_initial_state(tmp_path, driven_package, mpu, mode):
    raw, config, kind, jid = driven_package
    config = deepcopy(config)
    config["joints"][jid]["drive"] = actuator(kind, mode)
    package, _ = enrich_package(raw, config)
    saved = tmp_path / "driven.scadpkg"
    saved.write_bytes(scad.encode_product_package(package))
    # New process reads the package itself, independently of in-memory objects.
    code = "from pathlib import Path;import simplecadapi as s,sys,json; p=s.read_product_package(data=Path(sys.argv[1])); c=p.root_definition.metadata['sca_physics']; assert c['schema_version']=='1.8'; expected=json.loads(sys.argv[3]); assert all(c['joints'][sys.argv[2]][k]==v for k,v in expected.items())"
    subprocess.run([sys.executable, "-I", "-B", "-c", code, str(saved), jid, json.dumps(config["joints"][jid])], check=True, capture_output=True, text=True)
    output = tmp_path / "driven.usda"
    report = export_package(saved, output, config=ExportConfig(meters_per_unit=mpu))
    validate_usd(output)
    joint = report["physics"]["joints"][0]
    stage = Usd.Stage.Open(str(output))
    prim = stage.GetPrimAtPath(joint["prim_path"])
    angular = kind == "revolute"
    drive = UsdPhysics.DriveAPI(prim, "angular" if angular else "linear")
    factor = 180 / math.pi if angular else 1 / mpu
    gain = math.pi / 180 / mpu**2 if angular else 1
    assert drive.GetStiffnessAttr().Get() == pytest.approx((2 if mode == "position" else 0) * gain)
    assert drive.GetDampingAttr().Get() == pytest.approx((.01 if mode == "off" else .04) * gain)
    assert drive.GetTargetVelocityAttr().Get() == pytest.approx(0 if mode == "off" else (.2 if angular else .003) * .75 * factor)
    if mode != "off":
        assert drive.GetMaxForceAttr().Get() == pytest.approx(.4 / mpu**(2 if angular else 1))
        response = update_drive_targets(stage, jid, target_velocity=.1)
        assert response["authored_targets"]["velocity"] == pytest.approx(.1 * .75 * factor)
    else:
        assert not drive.GetMaxForceAttr().HasAuthoredValueOpinion()
        with pytest.raises(ValueError, match="no active"):
            update_drive_targets(stage, jid, target_velocity=.1)
    state = joint["physx_extensions"]["initial_state"]
    assert prim.GetAttribute(state["position_attribute"]).Get() == pytest.approx(23 if angular else .003 / mpu)
    assert prim.GetAttribute("physxJoint:maxJointVelocity").Get() == pytest.approx(.5 * factor)
    assert prim.GetAttribute("physxJoint:jointFriction").Get() == pytest.approx(.05)
    articulation = report["physics"]["articulation"]
    assert articulation["expected_link_count"] == 2
    assert articulation["expected_dof_count"] == 1
    assert UsdPhysics.FixedJoint.Get(stage, articulation["synthetic_world_fixed"]["prim_path"])


def test_articulation_only_properties_reject_without_articulation(tmp_path, driven_package):
    raw, config, _, _ = driven_package
    config = deepcopy(config)
    config.pop("assembly")
    package, _ = enrich_package(raw, config)
    with pytest.raises(ValueError, match="requires assembly.articulation"):
        export_package(package, tmp_path / "invalid.usda")


@pytest.mark.parametrize("tamper", ["state", "max_velocity", "friction", "max_force", "root"])
def test_new_joint_physics_tampering_is_detected(tmp_path, driven_package, tamper):
    raw, config, _, _ = driven_package
    package, _ = enrich_package(raw, config)
    output = tmp_path / "driven.usda"
    report = export_package(package, output)
    stage = Usd.Stage.Open(str(output))
    joint = report["physics"]["joints"][0]
    prim = stage.GetPrimAtPath(joint["prim_path"])
    if tamper == "root":
        stage.GetPrimAtPath(report["physics"]["articulation"]["root_prim_path"]).RemoveAPI(UsdPhysics.ArticulationRootAPI)
    elif tamper == "state":
        prim.GetAttribute(joint["physx_extensions"]["initial_state"]["position_attribute"]).Set(0)
    else:
        attr = {"max_velocity": "physxJoint:maxJointVelocity", "friction": "physxJoint:jointFriction",
                "max_force": "drive:" + joint["effective_drive"]["name"] + ":physics:maxForce"}[tamper]
        prim.GetAttribute(attr).Set(20)
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError):
        validate_usd(output)


@pytest.mark.parametrize("mpu", [1, .01])
def test_fixed_root_world_frame_retains_nonorigin_pose(mpu):
    from test_physics import _fixture, _joint
    from sca_usd_export.embedded_physics import legacy_settings
    from sca_usd_export.physics import apply_physics
    from sca_usd_export.stage_units import convert_stage_units
    stage, records, graph = _fixture(("base", "link"), positions=[(.12, .23, .34), (.12, .23, .34)])
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    for record in records[1:]:
        record["world_matrix"][:3, :3] = rotation
    graph.ground_edges.append({"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"})
    _joint(graph, "revolute", "base", "link")
    settings = legacy_settings(records, graph, ExportConfig())
    settings["assembly"]["articulation"] = {"enabled": sourced(True, "boolean"),
        "root_node_id": "node/fixture", "fixed_base": sourced(True, "boolean")}
    report = apply_physics(stage, records, graph, ExportConfig(), settings)
    convert_stage_units(stage, mpu)
    joint = UsdPhysics.FixedJoint.Get(stage, report["articulation"]["root_prim_path"])
    assert joint.GetLocalPos0Attr().Get() == pytest.approx(np.array([.12, .23, .34]) / mpu)
    q = joint.GetLocalRot0Attr().Get()
    assert abs(q.GetReal()) == pytest.approx(math.sqrt(.5))
    assert abs(q.GetImaginary()[2]) == pytest.approx(math.sqrt(.5))


@pytest.mark.parametrize("mpu", [1, .01])
def test_mimic_conversion_no_follower_drive_and_tamper_detection(tmp_path, mpu):
    from test_physics import _fixture, _joint
    from sca_usd_export.embedded_physics import legacy_settings
    from sca_usd_export.physics import apply_physics
    from sca_usd_export.physics_validation import validate_physics
    from sca_usd_export.stage_units import convert_stage_units, stage_in_si
    stage, records, graph = _fixture(("base", "pinion", "rack"), positions=[(0, 0, 0)] * 3)
    records[-1]["world_matrix"][2, 3] = .003
    theta = -.5
    records[-2]["world_matrix"][:3, :3] = [[math.cos(theta), -math.sin(theta), 0],
        [math.sin(theta), math.cos(theta), 0], [0, 0, 1]]
    graph.ground_edges.append({"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"})
    _joint(graph, "revolute", "base", "pinion", identifier="reference")
    _joint(graph, "prismatic", "base", "rack", identifier="follower")
    settings = legacy_settings(records, graph, ExportConfig())
    settings["assembly"]["articulation"] = {"enabled": sourced(True, "boolean"),
        "root_node_id": "node/fixture", "fixed_base": sourced(True, "boolean")}
    settings["couplings"] = {"rack": {"representation": "mimic", "reference_joint_id": "reference",
        "follower_joint_id": "follower", "gearing": sourced(.006, "m/rad"),
        "offset": sourced(0, "m"), "source": sourced(0, "m")["source"]}}
    report = apply_physics(stage, records, graph, ExportConfig(), settings)
    coupling = report["couplings"][0]
    audit = convert_stage_units(stage, mpu)
    prim = stage.GetPrimAtPath(coupling["follower_path"])
    assert not UsdPhysics.DriveAPI.GetAll(prim)
    assert prim.GetAttribute("physxMimicJoint:rotX:gearing").Get() == pytest.approx(.006 * math.pi / 180 / mpu)
    assert abs(coupling["initial_residual_m"]) < 1e-12
    validate_physics(stage_in_si(stage, unit_report=audit), report, tolerance=1e-8)
    prim.GetAttribute("physxMimicJoint:rotX:gearing").Set(1)
    with pytest.raises(ValueError):
        validate_physics(stage_in_si(stage), report, tolerance=1e-8)
