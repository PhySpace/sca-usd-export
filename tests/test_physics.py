"""Exercise real USD schemas and BREP mass properties without a simulator."""

from types import SimpleNamespace

import numpy as np
import pytest

import pxr
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

import simplecadapi as scad
from simplecadapi.product.placement import Placement, placement_ticks

from sca_usd_export.config import ExportConfig
from sca_usd_export.physics import apply_physics


def _part(identifier, *, density=1000.0):
    body = scad.make_box_rsolid(width=10.0, height=10.0, depth=10.0)
    part = scad.make_part_rpart(identifier, body)
    if density is not None:
        material = scad.make_material_rmaterial(
            identifier + "_material", density=density, density_unit="kg/m^3"
        )
        part = scad.assign_material_rpart(part, material)
    return part


def _fixture(
    part_names=("base", "door", "handle"), positions=None, *, root_is_part=False
):
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/Model")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    stage.CreateClassPrim("/Model/Definitions")
    mesh = UsdGeom.Mesh.Define(stage, "/Model/Definitions/Box/Mesh")
    vertices, triangles = scad.exporter.tessellate_solid(
        _part("prototype").body.wrapped,
        linear_deflection=0.1,
        angular_deflection_degrees=20,
        relative=False,
    )
    mesh.CreatePointsAttr([Gf.Vec3f(*point) for point in vertices * 0.001])
    mesh.CreateFaceVertexCountsAttr([3] * len(triangles))
    mesh.CreateFaceVertexIndicesAttr(triangles.reshape(-1).tolist())
    mesh.CreateSubdivisionSchemeAttr("none")
    records = []
    if not root_is_part:
        UsdGeom.Xform.Define(stage, "/Model/Structure")
        records.append(
            dict(
                node_id="node/fixture",
                parent_node_id=None,
                definition_id="fixture",
                kind="assembly",
                prim_path="/Model/Structure",
                item=None,
                world_matrix=np.eye(4),
            )
        )
    for index, name in enumerate(part_names):
        node_id = "node/fixture/" + name
        path = "/Model/Structure/" + name
        prim = UsdGeom.Xform.Define(stage, path)
        UsdGeom.Mesh.Define(
            stage, path + "/Mesh"
        ).GetPrim().GetReferences().AddInternalReference(mesh.GetPath())
        matrix = np.eye(4)
        matrix[:3, 3] = (
            positions[index] if positions is not None else (index * 0.02, 0, 0)
        )
        UsdGeom.Xformable(prim).AddTransformOp().Set(Gf.Matrix4d(matrix.T.tolist()))
        records.append(
            dict(
                node_id=node_id,
                parent_node_id=None if root_is_part else "node/fixture",
                definition_id=name,
                kind="part",
                prim_path=path,
                mesh_path=path + "/Mesh",
                definition_mesh_path=str(mesh.GetPath()),
                item=_part(name),
                world_matrix=matrix,
                volume_m3=1e-6,
                center_of_mass_local_m=np.array([0, 0, 0.005]),
            )
        )
    graph = SimpleNamespace(
        root_node_id=records[0]["node_id"], ground_edges=[], connectors=[], joints=[]
    )
    return stage, records, graph


def _connector(graph, name, *, frame=None):
    node_id = "node/fixture/" + name
    identifier = "connector/" + name
    graph.connectors.append(
        dict(
            connector_snapshot_id=identifier,
            node_id=node_id,
            local_frame=placement_ticks(frame or Placement((0, 0, 0))),
            source_connector_snapshot_id=None,
        )
    )
    return {"connector_snapshot_id": identifier}


def _joint(graph, kind, first, second, *, limits=None, identifier=None):
    endpoints = {}
    for key, name in (("connector_a", first), ("connector_b", second)):
        snapshot = "connector/" + name
        endpoints[key] = next(
            (
                {"connector_snapshot_id": snapshot}
                for c in graph.connectors
                if c["connector_snapshot_id"] == snapshot
            ),
            None,
        ) or _connector(graph, name)
    graph.joints.append(
        dict(
            joint_id=identifier or kind + "_" + first + "_" + second,
            joint_type=kind,
            limits=limits,
            parameters={},
            **endpoints,
        )
    )


def test_fixed_handle_shares_door_body_and_mass_inertia_match_brep(tmp_path):
    stage, records, graph = _fixture(positions=[(0, 0, 0), (0.02, 0, 0), (0.04, 0, 0)])
    graph.ground_edges.append(
        {"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"}
    )
    _joint(graph, "fixed", "door", "handle")
    _joint(
        graph,
        "revolute",
        "base",
        "door",
        limits={"angle_limit": {"lower_value": -90, "upper_value": 45}},
    )
    report = apply_physics(stage, records, graph, ExportConfig())
    assert report["dynamic_body_count"] == 1
    assert report["static_group_count"] == 1
    assert report["collider_count"] == 3
    assert report["joint_count"] == 1
    moving = next(group for group in report["groups"] if group["dynamic"])
    assert len(moving["parts"]) == 2
    assert moving["mass_kg"] == pytest.approx(0.002)
    assert moving["center_of_mass_body_m"] == pytest.approx([0.01, 0, 0.005])
    expected = np.diag(
        [
            2 * (0.001 * (0.01**2 + 0.01**2) / 12),
            2 * (0.001 * (0.01**2 + 0.01**2) / 12 + 0.001 * 0.01**2),
            2 * (0.001 * (0.01**2 + 0.01**2) / 12 + 0.001 * 0.01**2),
        ]
    )
    np.testing.assert_allclose(
        moving["inertia_tensor_body_kg_m2"], expected, rtol=1e-6, atol=1e-16
    )
    body = stage.GetPrimAtPath(moving["body_path"])
    assert body.HasAPI(UsdPhysics.RigidBodyAPI)
    np.testing.assert_allclose(
        UsdPhysics.MassAPI(body).GetDiagonalInertiaAttr().Get(),
        np.linalg.eigvalsh(expected),
        rtol=1e-6,
    )
    for part in moving["parts"]:
        original = stage.GetPrimAtPath(part["semantic_path"])
        physical = stage.GetPrimAtPath(part["shape_path"])
        assert UsdGeom.Imageable(original).ComputeVisibility() == "invisible"
        assert UsdGeom.Imageable(physical).ComputeVisibility() == "inherited"
        assert physical.HasAPI(UsdPhysics.CollisionAPI)
        assert physical.GetRelationship("material:binding:physics").GetTargets()
        assert original.GetRelationship("simplecad:physicsBody").GetTargets() == [
            body.GetPath()
        ]
        record = next(r for r in records if r["node_id"] == part["node_id"])
        world = UsdGeom.XformCache().GetLocalToWorldTransform(physical)
        np.testing.assert_allclose(
            np.array(world).T, record["world_matrix"], atol=1e-12
        )
    joint = UsdPhysics.RevoluteJoint.Get(stage, report["joints"][0]["prim_path"])
    assert joint.GetAxisAttr().Get() == "Z"
    assert joint.GetLowerLimitAttr().Get() == -90  # USD uses degrees, not radians.
    assert joint.GetUpperLimitAttr().Get() == 45
    assert not report["simulation_verified"]
    # Real serialization/reopening checks composition and authored API schemas.
    destination = str(tmp_path / "physics.usda")
    stage.GetRootLayer().Export(destination)
    reopened = Usd.Stage.Open(destination)
    assert reopened.GetPrimAtPath(moving["parts"][0]["shape_path"]).HasAPI(
        UsdPhysics.CollisionAPI
    )


def test_prismatic_limits_and_connector_ticks_are_converted_to_metres():
    stage, records, graph = _fixture(
        ("base", "slider"), positions=[(0, 0, 0), (0, 0, 0.005)]
    )
    graph.ground_edges.append(
        {"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"}
    )
    _connector(graph, "base", frame=Placement((1, 2, 3)))
    _connector(graph, "slider", frame=Placement((1, 2, 3)))
    _joint(
        graph,
        "prismatic",
        "base",
        "slider",
        limits={"distance_limit": {"lower_value": -5, "upper_value": 10}},
    )
    report = apply_physics(stage, records, graph, ExportConfig())
    joint = UsdPhysics.PrismaticJoint.Get(stage, report["joints"][0]["prim_path"])
    assert joint.GetLowerLimitAttr().Get() == pytest.approx(-0.005)
    assert joint.GetUpperLimitAttr().Get() == pytest.approx(0.01)
    assert joint.GetLocalPos0Attr().Get() == pytest.approx((0.001, 0.002, 0.003))
    assert joint.GetLocalPos1Attr().Get() == pytest.approx((0.001, 0.002, 0.003))


def test_unconnected_parts_are_static_and_density_fallback_is_reported():
    stage, records, graph = _fixture(("loose",))
    records[-1]["item"] = _part("loose", density=None)
    report = apply_physics(
        stage, records, graph, ExportConfig(default_density_kg_m3=2500)
    )
    assert report["dynamic_body_count"] == 0
    assert report["static_group_count"] == 1
    assert report["groups"][0]["mass_kg"] == pytest.approx(0.0025)
    assert not stage.GetPrimAtPath(report["groups"][0]["body_path"]).HasAPI(
        UsdPhysics.RigidBodyAPI
    )
    assert (
        next(item for item in report["defaults"] if item["field"] == "density_kg_m3")[
            "value"
        ]
        == 2500
    )


def test_unsupported_coupling_freezes_connected_component_and_is_reported():
    stage, records, graph = _fixture(("base", "a", "b"))
    graph.ground_edges.append(
        {"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"}
    )
    _joint(graph, "revolute", "base", "a")
    _joint(graph, "revolute", "base", "b")
    _joint(graph, "gear", "a", "b")
    report = apply_physics(stage, records, graph, ExportConfig())
    assert report["dynamic_body_count"] == 0
    assert report["joint_count"] == 0
    assert report["unsupported"][0]["joint_type"] == "gear"
    assert all(
        group["reason"] == "unsupported_relation_frozen" for group in report["groups"]
    )


def test_public_connector_resolves_leaf_ownership_without_rigidifying_wrapper():
    stage, records, graph = _fixture(("base", "door"))
    graph.ground_edges.append(
        {"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"}
    )
    _joint(graph, "revolute", "base", "door")
    public_id = "connector/public_door"
    graph.connectors.append(
        dict(
            connector_snapshot_id=public_id,
            node_id="node/fixture",
            source_connector_snapshot_id="connector/door",
            local_frame=placement_ticks(Placement((0, 0, 0))),
        )
    )
    graph.joints[0]["connector_b"] = {"connector_snapshot_id": public_id}
    report = apply_physics(stage, records, graph, ExportConfig())
    assert report["dynamic_body_count"] == 1
    assert report["joints"][0]["body1_group"] == "node/fixture/door"
    assert any(item["field"] == "limits" for item in report["defaults"])


@pytest.mark.parametrize(
    "invalid",
    [
        "nan_transform",
        "scale_transform",
        "volume",
        "missing_mesh",
        "density_unit",
        "wrong_stage_units",
    ],
)
def test_invalid_physics_input_fails_clearly(invalid):
    stage, records, graph = _fixture(("loose",))
    if invalid == "nan_transform":
        records[-1]["world_matrix"][0, 3] = float("nan")
    elif invalid == "scale_transform":
        records[-1]["world_matrix"][0, 0] = 2
    elif invalid == "volume":
        records[-1]["volume_m3"] = 0
    elif invalid == "missing_mesh":
        records[-1]["definition_mesh_path"] = "/absent"
    elif invalid == "density_unit":
        records[-1]["item"] = scad.assign_material_rpart(
            records[-1]["item"],
            scad.make_material_rmaterial("bad_unit", density=1, density_unit="unknown"),
        )
    elif invalid == "wrong_stage_units":
        UsdGeom.SetStageMetersPerUnit(stage, 0.001)
    with pytest.raises(ValueError):
        apply_physics(stage, records, graph, ExportConfig())


def test_reapplying_physics_refuses_to_overwrite_existing_scene():
    stage, records, graph = _fixture(("loose",))
    apply_physics(stage, records, graph, ExportConfig())
    with pytest.raises(ValueError, match="already exists"):
        apply_physics(stage, records, graph, ExportConfig())


def test_explicit_dynamic_policy_applies_to_a_single_part_root():
    stage, records, graph = _fixture(("loose",), root_is_part=True)
    report = apply_physics(
        stage, records, graph, ExportConfig(unconnected_body_policy="dynamic")
    )
    assert report["dynamic_body_count"] == 1
    assert report["groups"][0]["reason"] == "explicit_dynamic_policy"


def test_usd_physics_parser_resolves_static_target_frame_to_world():
    stage, records, graph = _fixture(
        ("base", "door"), positions=[(0.1, 0.2, 0.3), (0.1, 0.2, 0.3)]
    )
    graph.ground_edges.append(
        {"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"}
    )
    # Both solved bodies rotate 90 degrees about X. Their connector Z axis is
    # world -Y; a static target must be converted to its complete world frame.
    rotation = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    for record in records[1:]:
        record["world_matrix"][:3, :3] = rotation
    _connector(graph, "base", frame=Placement((1, 2, 3)))
    _connector(graph, "door", frame=Placement((1, 2, 3)))
    _joint(graph, "revolute", "base", "door")
    report = apply_physics(stage, records, graph, ExportConfig())
    parsed = UsdPhysics.UsdPhysicsLoadStageFromPrimRange(stage, [Sdf.Path("/")])
    assert all(
        desc.isValid for _, descriptions in parsed.values() for desc in descriptions
    )
    descriptor = parsed[UsdPhysics.ObjectType.RevoluteJoint][1][0]
    assert not descriptor.body0  # USD resolves the static target to world.
    assert descriptor.body1
    assert descriptor.localPose0Position == pytest.approx((0.101, 0.197, 0.302))
    assert descriptor.localPose1Position == pytest.approx((0.001, 0.002, 0.003))
    expected_axis_world = Gf.Rotation(descriptor.localPose0Orientation).TransformDir(
        Gf.Vec3d(0, 0, 1)
    )
    assert expected_axis_world == pytest.approx((0, -1, 0), abs=1e-6)
    assert report["joint_count"] == 1


def test_every_demonstration_default_records_value_unit_and_reason():
    stage, records, graph = _fixture(("loose",))
    records[-1]["item"] = _part("loose", density=None)
    report = apply_physics(stage, records, graph, ExportConfig())
    assert report["defaults"]
    assert all(
        {"field", "value", "unit", "reason", "source"} <= set(item)
        for item in report["defaults"]
    )
    assert all(
        "node_id" in item or "group_id" in item or "prim_path" in item
        for item in report["defaults"]
    )


def test_prismatic_passive_damping_uses_si_force_drive_without_angular_conversion():
    from sca_usd_export.embedded_physics import legacy_settings

    stage, records, graph = _fixture(("base", "slider"))
    graph.ground_edges.append({"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"})
    _joint(graph, "prismatic", "base", "slider")
    config = ExportConfig()
    settings = legacy_settings(records, graph, config)
    settings["joints"][graph.joints[0]["joint_id"]]["damping"] = {
        "value": 2.5, "unit": "N*s/m", "source": {"kind": "explicit", "detail": "test linear passive damping"}}
    report = apply_physics(stage, records, graph, config, settings)
    drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath(report["joints"][0]["prim_path"]), "linear")
    assert drive.GetDampingAttr().Get() == pytest.approx(2.5)
    assert drive.GetStiffnessAttr().Get() == 0
    assert drive.GetTargetVelocityAttr().Get() == 0
    assert drive.GetTypeAttr().Get() == "force"


def test_joint_collision_false_filters_static_and_dynamic_actual_shapes():
    stage, records, graph = _fixture(("base", "arm"))
    graph.ground_edges.append({"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"})
    _joint(graph, "revolute", "base", "arm")
    report = apply_physics(stage, records, graph, ExportConfig())
    assert report["dynamic_body_count"] == 1
    assert len(report["collision_exclusions"]) == 1
    pair = report["collision_exclusions"][0]
    assert pair["reasons"][0]["origin"] == "joint.collision_enabled=false"
    assert stage.GetPrimAtPath(pair["a_path"]).HasAPI(UsdPhysics.FilteredPairsAPI)
    assert stage.GetPrimAtPath(pair["a_path"]).GetRelationship("physics:filteredPairs").GetTargets() == [Sdf.Path(pair["b_path"])]


def test_joint_collision_true_omits_derived_filter():
    stage, records, graph = _fixture(("base", "arm"))
    graph.ground_edges.append({"parent_node_id": "node/fixture", "child_node_id": "node/fixture/base"})
    _joint(graph, "revolute", "base", "arm")
    report = apply_physics(stage, records, graph, ExportConfig(joint_collision_enabled=True))
    assert report["collision_exclusions"] == []
