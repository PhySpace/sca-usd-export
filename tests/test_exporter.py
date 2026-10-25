"""Integration regressions built through genuine SCA definitions and packages."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import numpy as np
import pytest
import simplecadapi as scad
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from sca_usd_export.config import ExportConfig
from sca_usd_export.dependencies import require_usd
from sca_usd_export.exporter import export_package
from sca_usd_export.model import transformed_points
from sca_usd_export.validation import validate_usd


def test_center_of_mass_uses_usd_float32_rounding(tmp_path):
    @scad.part(id="usd-float32-com")
    def box():
        return scad.make_box_rsolid(width=16.0, height=8.0, depth=8.0,
                                   bottom_face_center=(258.0, 0.0, 0.0))

    package = scad.build_product_package(box())
    output = tmp_path / "float-com.usda"
    report = export_package(package, output)
    analytical = np.asarray(report["physics"]["groups"][0]["center_of_mass_body_m"])
    assert analytical[0] == pytest.approx(.258)
    stage = Usd.Stage.Open(str(output))
    actor = stage.GetPrimAtPath(report["physics"]["groups"][0]["body_path"])
    actual = np.asarray(UsdPhysics.MassAPI(actor).GetCenterOfMassAttr().Get())
    assert np.array_equal(actual, analytical.astype(np.float32))
    assert abs(actual[0] - analytical[0]) > report["configuration"]["geometry_absolute_tolerance_m"]
    assert validate_usd(output)["ok"]


@pytest.fixture(scope="module")
def nested_package():
    @scad.part(id="usd-test-pin")
    def pin():
        return scad.make_part_rpart(
            part_id="usd-test-pin",
            name="Small pin",
            body=scad.make_box_rsolid(width=2.0, height=4.0, depth=8.0),
        )

    definition = pin()

    @scad.assemble(id="usd-test-arm", definitions=(definition,))
    def arm():
        assembly = scad.make_assembly_rassembly(assembly_id="usd-test-arm", name="Arm")
        return scad.add_component_rassembly(
            assembly,
            definition.value,
            "pin",
            scad.make_placement_rplacement(origin=(5.0, 0.0, 0.0)),
            name="Nested pin",
        )

    child = arm()

    @scad.assemble(id="usd-test-root", definitions=(definition, child))
    def root():
        assembly = scad.make_assembly_rassembly(
            assembly_id="usd-test-root", name="Test assembly"
        )
        assembly = scad.add_component_rassembly(
            assembly,
            child.value,
            "arm",
            scad.make_placement_rplacement(
                origin=(100.0, 0.0, 0.0),
                x_axis=(0.0, 1.0, 0.0),
                y_axis=(-1.0, 0.0, 0.0),
            ),
            name="Rotated arm",
        )
        return scad.add_component_rassembly(
            assembly,
            definition.value,
            "spare",
            scad.make_placement_rplacement(origin=(0.0, 50.0, 0.0)),
            name="Spare pin",
        )

    return scad.build_product_package(root())


@pytest.fixture(scope="module")
def exported(tmp_path_factory, nested_package):
    output = tmp_path_factory.mktemp("usd-integrated") / "nested.usda"
    report = export_package(nested_package, output)
    return output, report


def test_nested_transforms_definition_reuse_and_physical_render_pose(exported):
    output, report = exported
    assert report["status"] == {
        "file_written": True,
        "geometry_validated": True,
        "physics_static_validated": True,
        "simulator_validated": False,
    }
    assert report["calculated"]["part_occurrence_count"] == 2
    assert report["calculated"]["unique_part_definition_count"] == 1
    assert report["calculated"]["triangle_count"] == 24
    stage = Usd.Stage.Open(str(output))
    by_id = {item["node_id"]: item for item in report["occurrences"]}
    nested = by_id["node/usd-test-root/arm/pin"]
    spare = by_id["node/usd-test-root/spare"]
    assert len(by_id) == 4
    np.testing.assert_allclose(
        np.asarray(nested["world_matrix_m"])[:3, 3], [0.100, 0.005, 0], atol=1e-12
    )
    np.testing.assert_allclose(
        np.asarray(nested["local_matrix_m"])[:3, 3], [0.005, 0, 0], atol=1e-12
    )
    np.testing.assert_allclose(
        np.asarray(spare["world_matrix_m"])[:3, 3], [0, 0.050, 0], atol=1e-12
    )
    assert nested["definition_mesh_path"] == spare["definition_mesh_path"]
    cache = UsdGeom.XformCache()
    definition_points = np.asarray(
        UsdGeom.Mesh(stage.GetPrimAtPath(nested["definition_mesh_path"]))
        .GetPointsAttr()
        .Get()
    )
    # Each dimension is converted from mm to metres exactly once.
    np.testing.assert_allclose(
        np.ptp(definition_points, axis=0), [0.002, 0.004, 0.008], atol=1e-9
    )
    for record in (nested, spare):
        prim = stage.GetPrimAtPath(record["prim_path"])
        assert prim.GetAttribute("sca:definition_id").Get() == "usd-test-pin"
        assert prim.GetDisplayName() == record["display_name"]
        reference = stage.GetPrimAtPath(record["prim_path"] + "/Geometry")
        arcs = reference.GetMetadata("references").GetAddedOrExplicitItems()
        assert (
            len(arcs) == 1
            and str(arcs[0].primPath)
            == record["definition_mesh_path"].rsplit("/", 1)[0]
        )
        shape_path = prim.GetRelationship("simplecad:physicsGeometry").GetTargets()[0]
        physical = stage.GetPrimAtPath(shape_path)
        physical_world = transformed_points(
            np.asarray(UsdGeom.Mesh(physical).GetPointsAttr().Get()),
            np.asarray(cache.GetLocalToWorldTransform(physical)).T,
        )
        expected_world = transformed_points(definition_points, record["world_matrix_m"])
        np.testing.assert_allclose(physical_world, expected_world, atol=1e-9)
        assert UsdGeom.Imageable(prim).ComputeVisibility() == "invisible"
        assert UsdGeom.Imageable(physical).ComputeVisibility() == "inherited"
    assert report["physics"]["dynamic_body_count"] == 0
    assert report["physics"]["static_group_count"] == 2
    assert report["physics"]["joint_count"] == 0


def test_static_physics_parser_accepts_exported_shapes(exported):
    stage = Usd.Stage.Open(str(exported[0]))
    parsed = UsdPhysics.UsdPhysicsLoadStageFromPrimRange(
        stage, [Sdf.Path("/Model/Physics")]
    )
    paths, descriptors = parsed[UsdPhysics.ObjectType.MeshShape]
    assert len(paths) == 2
    assert all(item.isValid for item in descriptors)
    assert all(not item.rigidBody for item in descriptors)
    assert len(parsed[UsdPhysics.ObjectType.Scene][0]) == 1


def test_root_solved_snapshot_keeps_occurrence_specific_nested_pose(tmp_path):
    @scad.part(id="usd-snapshot-ring")
    def ring():
        part = scad.make_part_rpart(
            part_id="usd-snapshot-ring",
            body=scad.make_cylinder_rsolid(radius=2.0, height=1.0),
        )
        return scad.add_connector_rpart(
            part,
            scad.make_placement_connector_rconnector(
                "axis", scad.identity_placement_rplacement()
            ),
        )

    part = ring()

    @scad.assemble(id="usd-snapshot-bearing", definitions=(part,))
    def bearing():
        assembly = scad.make_assembly_rassembly(assembly_id="usd-snapshot-bearing")
        for component_id in ("outer", "inner"):
            assembly = scad.add_component_rassembly(
                assembly,
                part.value,
                component_id,
                scad.identity_placement_rplacement(),
            )
        assembly = scad.ground_component_rassembly(assembly, "outer")
        assembly = scad.add_revolute_constraint_rassembly(
            assembly,
            "free-spin",
            scad.make_connector_ref_rconnectorref("outer", "axis"),
            scad.make_connector_ref_rconnectorref("inner", "axis"),
        )
        assembly = scad.set_public_connector_rassembly(
            assembly,
            "outer_axis",
            "outer",
            "axis",
        )
        return scad.set_public_connector_rassembly(
            assembly, "inner_axis", "inner", "axis"
        )

    child = bearing()
    np.testing.assert_allclose(
        child.value.get_component("inner").placement.x_axis, [1, 0, 0]
    )
    quarter = scad.make_placement_rplacement(
        origin=(0, 0, 0),
        x_axis=(0, 1, 0),
        y_axis=(-1, 0, 0),
    )

    @scad.assemble(id="usd-snapshot-root", definitions=(child, part))
    def fixture():
        assembly = scad.make_assembly_rassembly(assembly_id="usd-snapshot-root")
        for component_id, item, frame in (
            ("housing", part.value, scad.identity_placement_rplacement()),
            ("shaft", part.value, quarter),
            ("bearing", child.value, scad.identity_placement_rplacement()),
        ):
            assembly = scad.add_component_rassembly(assembly, item, component_id, frame)
        assembly = scad.ground_component_rassembly(assembly, "housing")
        assembly = scad.ground_component_rassembly(assembly, "shaft")
        assembly = scad.add_fixed_constraint_rassembly(
            assembly,
            "outer-to-housing",
            scad.make_connector_ref_rconnectorref("housing", "axis"),
            scad.make_connector_ref_rconnectorref("bearing", "outer_axis"),
        )
        return scad.add_fixed_constraint_rassembly(
            assembly,
            "inner-to-shaft",
            scad.make_connector_ref_rconnectorref("shaft", "axis"),
            scad.make_connector_ref_rconnectorref("bearing", "inner_axis"),
        )

    result = fixture()
    root_inner = result.value.get_component("bearing").item.get_component("inner")
    np.testing.assert_allclose(root_inner.placement.x_axis, [0, 1, 0], atol=1e-7)
    # The shared child definition still represents its own identity reference
    # pose; only this root occurrence's verified snapshot rotates the inner ring.
    np.testing.assert_allclose(
        child.value.get_component("inner").placement.x_axis, [1, 0, 0]
    )
    report = export_package(
        scad.build_product_package(result), tmp_path / "snapshot.usda"
    )
    inner = next(
        item
        for item in report["occurrences"]
        if item["node_id"] == "node/usd-snapshot-root/bearing/inner"
    )
    np.testing.assert_allclose(
        np.asarray(inner["world_matrix_m"])[:3, 0], [0, 1, 0], atol=1e-7
    )
    standalone_authored_x = inner["authored_transform_ticks"]["x_axis"]
    assert standalone_authored_x == [1000000000, 0, 0]
    assert report["validation"]["ok"]


def test_explicit_config_is_used_and_reported(tmp_path, nested_package):
    config = ExportConfig(
        up_axis="Z",
        default_density_kg_m3=2500.0,
        default_friction=0.3,
        gravity_m_s2=0.0,
    )
    output = tmp_path / "configured.usda"
    report = export_package(nested_package, output, config=config)
    assert report["configuration"] == config.to_dict()
    stage = Usd.Stage.Open(str(output))
    assert UsdGeom.GetStageUpAxis(stage) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert UsdPhysics.GetStageKilogramsPerUnit(stage) == 1.0
    scene = UsdPhysics.Scene(stage.GetPrimAtPath("/Model/Physics/Scene"))
    assert scene.GetGravityMagnitudeAttr().Get() == 0.0
    assert scene.GetGravityDirectionAttr().Get() == Gf.Vec3f(0, 0, -1)
    for group in report["physics"]["groups"]:
        assert group["reason"] == "unconnected_static_policy"
        assert group["mass_kg"] == pytest.approx(2500.0 * 2 * 4 * 8 * 1e-9)
        assert group["parts"][0]["density_source"] == "config.default_density_kg_m3"
    assert (
        len(
            [
                item
                for item in report["physics"]["defaults"]
                if item["field"] == "density_kg_m3"
            ]
        )
        == 2
    )
    assert report["status"]["simulator_validated"] is False


@pytest.mark.parametrize("occupied", ["stage", "report", "history"])
def test_existing_outputs_are_never_overwritten(tmp_path, occupied):
    destination = tmp_path / "protected.usda"
    protected = {
        "stage": destination,
        "report": destination.with_suffix(".report.json"),
        "history": destination.with_suffix(".history"),
    }[occupied]
    if occupied == "history":
        protected.mkdir()
        sentinel = protected / "keep.txt"
    else:
        sentinel = protected
    sentinel.write_bytes(b"user content")
    with pytest.raises(FileExistsError, match="already exists"):
        export_package(None, destination)
    assert sentinel.read_bytes() == b"user content"
    if occupied != "stage":
        assert not destination.exists()


def test_missing_input_and_wrong_suffix_fail_before_creating_output(tmp_path):
    output = tmp_path / "new.usda"
    with pytest.raises(FileNotFoundError, match="Input .scadpkg does not exist"):
        export_package(tmp_path / "absent.scadpkg", output)
    with pytest.raises(ValueError, match="output must end"):
        export_package(None, tmp_path / "wrong.usd")
    invalid_input = tmp_path / "model.stl"
    invalid_input.write_bytes(b"not a product package")
    with pytest.raises(ValueError, match="input must end"):
        export_package(invalid_input, output)
    assert not output.exists()


def test_dependency_failure_explains_independent_install(monkeypatch):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "pxr" or name.startswith("pxr."):
            raise ImportError("test: OpenUSD unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(ValueError, match="extension dependencies.*provision"):
        require_usd()


@pytest.mark.parametrize(
    "field,value",
    [
        ("default_density_kg_m3", -1.0),
        ("linear_deflection_mm", float("nan")),
        ("angular_deflection_degrees", 181.0),
        ("default_friction", -0.5),
        ("default_restitution", 1.5),
        ("up_axis", "X"),
    ],
)
def test_invalid_config_rejected(field, value):
    with pytest.raises(ValueError):
        ExportConfig(**{field: value})


@pytest.mark.parametrize("tamper", ["points", "transform", "report"])
def test_validation_rejects_changed_geometry_transforms_and_report(
    tmp_path, nested_package, tamper
):
    output = tmp_path / "tampered.usda"
    report = export_package(nested_package, output)
    if tamper == "report":
        report_path = output.with_suffix(".report.json")
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload["calculated"]["triangle_count"] += 1
        report_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        stage = Usd.Stage.Open(str(output))
        part = next(item for item in report["occurrences"] if item["kind"] == "part")
        if tamper == "points":
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(part["definition_mesh_path"]))
            points = mesh.GetPointsAttr().Get()
            points[0] = points[0] + Gf.Vec3f(0.001, 0, 0)
            mesh.GetPointsAttr().Set(points)
        else:
            prim = stage.GetPrimAtPath(part["prim_path"])
            UsdGeom.Xformable(prim).GetOrderedXformOps()[0].Set(Gf.Matrix4d(1))
        stage.GetRootLayer().Save()
    with pytest.raises(
        ValueError, match="extent|geometry changed|transform changed|triangle count"
    ):
        validate_usd(output)


@pytest.mark.parametrize(
    "tamper", ["collision_api", "collision_topology", "definition_id"]
)
def test_validation_rejects_changed_physics_and_component_identity(
    tmp_path, nested_package, tamper
):
    output = tmp_path / "physics-tampered.usda"
    report = export_package(nested_package, output)
    stage = Usd.Stage.Open(str(output))
    part = next(item for item in report["occurrences"] if item["kind"] == "part")
    semantic = stage.GetPrimAtPath(part["prim_path"])
    shape_path = semantic.GetRelationship("simplecad:physicsGeometry").GetTargets()[0]
    shape = stage.GetPrimAtPath(shape_path)
    if tamper == "collision_api":
        shape.RemoveAPI(UsdPhysics.CollisionAPI)
    elif tamper == "collision_topology":
        mesh = UsdGeom.Mesh(shape)
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        # All indices remain legal and triangles nondegenerate, but one source
        # triangle disappears and another is duplicated in the collider.
        indices[:3] = indices[3:6]
        mesh.GetFaceVertexIndicesAttr().Set(indices)
    else:
        semantic.GetAttribute("sca:definition_id").Set("wrong-definition")
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError):
        validate_usd(output)
