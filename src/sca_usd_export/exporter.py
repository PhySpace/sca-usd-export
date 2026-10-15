"""SCA package -> local definition meshes, semantic instances and USD Physics."""

import importlib.metadata
import json
from pathlib import Path

import numpy as np

from .config import ExportConfig
from .dependencies import require_usd
from .history import read_history_bundle, write_history_bundle
from .model import collect_model, transformed_points


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def write_new_json(path, payload):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            default=_json_default,
        )
        stream.write("\n")


def _identity(prim, source, Sdf):
    for field in (
        "node_id",
        "definition_id",
        "component_id",
        "instance_id",
        "node_kind",
    ):
        if source.get(field) is not None:
            prim.CreateAttribute("sca:" + field, Sdf.ValueTypeNames.String).Set(
                str(source[field])
            )
    for field, value in source.get("properties", {}).items():
        prim.CreateAttribute("sca:" + field, Sdf.ValueTypeNames.String).Set(str(value))
    prim.SetDisplayName(source["display_name"])


def export_package(data, output_path, *, config=None):
    """Export one validated ProductPackage (or .scadpkg path) to a new .usda.

    Output paths are never overwritten. All coordinates are authored in metres,
    retaining CAD axis directions and the materialized, solved local transforms.
    The semantic product tree and physics grouping are linked explicitly.
    """
    config = config or ExportConfig()
    if not isinstance(config, ExportConfig):
        raise ValueError("config must be ExportConfig")
    destination = Path(output_path).expanduser().resolve()
    if destination.suffix.lower() != ".usda":
        raise ValueError("output must end in .usda")
    report_path = destination.with_suffix(".report.json")
    history_dir = destination.with_suffix(".history")
    for path in (destination, report_path, history_dir):
        if path.exists():
            raise FileExistsError(f"Output already exists; choose a new name: {path}")
    if isinstance(data, (str, Path)):
        input_path = Path(data).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Input .scadpkg does not exist: {input_path}")
        if input_path.suffix.lower() != ".scadpkg":
            raise ValueError("input must end in .scadpkg")
    else:
        input_path = None
    Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt = require_usd()
    from simplecadapi.translator.package_units import (
        read_product_package_translation_units,
    )
    from .physics import apply_physics
    from .validation import validate_usd

    package, units = read_product_package_translation_units(data)
    if any(unit.definition.units != "mm" for unit in units):
        raise ValueError("this extension requires SCA canonical millimetre definitions")
    records, meshes = collect_model(package, config)

    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/Model").GetPrim()
    root.SetDisplayName(package.root_definition.metadata.get("name") or package.root_id)
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, getattr(UsdGeom.Tokens, config.up_axis.lower()))
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    stage.CreateClassPrim("/Model/Definitions")

    for definition_id, data_mesh in meshes.items():
        parent = data_mesh["definition_mesh_path"].rsplit("/", 1)[0]
        UsdGeom.Xform.Define(stage, parent)
        mesh = UsdGeom.Mesh.Define(stage, data_mesh["definition_mesh_path"])
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(data_mesh["points"]))
        triangles = data_mesh["triangles"]
        mesh.CreateFaceVertexCountsAttr(
            Vt.IntArray.FromNumpy(np.full(len(triangles), 3, dtype=np.int32))
        )
        mesh.CreateFaceVertexIndicesAttr(
            Vt.IntArray.FromNumpy(triangles.astype(np.int32).reshape(-1))
        )
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateOrientationAttr(UsdGeom.Tokens.rightHanded)
        mesh.CreateExtentAttr(
            Vt.Vec3fArray.FromNumpy(
                np.array(
                    [data_mesh["points"].min(axis=0), data_mesh["points"].max(axis=0)],
                    dtype=np.float32,
                )
            )
        )
        mesh.GetPrim().CreateAttribute(
            "sca:definition_id", Sdf.ValueTypeNames.String
        ).Set(definition_id)
        item = next(r["item"] for r in records if r["definition_id"] == definition_id)
        if item.material and item.material.color is not None:
            mesh.CreateDisplayColorAttr([Gf.Vec3f(*item.material.color)])

    expected_occurrences = []
    world_points = []
    for record in records:
        prim = UsdGeom.Xform.Define(stage, record["prim_path"]).GetPrim()
        _identity(prim, record["source_node"], Sdf)
        UsdGeom.Xformable(prim).AddTransformOp().Set(
            Gf.Matrix4d(record["local_matrix"].T.tolist())
        )
        expected = {
            key: record[key]
            for key in (
                "node_id",
                "parent_node_id",
                "definition_id",
                "kind",
                "prim_path",
                "display_name",
            )
        }
        expected["local_matrix_m"] = record["local_matrix"]
        expected["world_matrix_m"] = record["world_matrix"]
        expected["authored_transform_ticks"] = record["source_node"]["transform"]
        if record["kind"] == "part":
            mesh_data = meshes[record["definition_id"]]
            reference_parent = UsdGeom.Xform.Define(
                stage, record["prim_path"] + "/Geometry"
            ).GetPrim()
            reference_parent.GetReferences().AddInternalReference(
                mesh_data["definition_mesh_path"].rsplit("/", 1)[0]
            )
            # Native instances share definition geometry. Per-occurrence identity,
            # visibility and physics links live on the outer semantic Xform.
            reference_parent.SetInstanceable(True)
            expected.update(
                mesh_path=record["mesh_path"],
                definition_mesh_path=mesh_data["definition_mesh_path"],
                vertex_count=len(mesh_data["points"]),
                triangle_count=len(mesh_data["triangles"]),
                geometry_sha256=mesh_data["geometry_sha256"],
                bounds_local_m=mesh_data["bounds_local_m"],
                volume_m3=mesh_data["volume_m3"],
            )
            points_world = transformed_points(
                mesh_data["source_points_m"], record["world_matrix"]
            )
            expected["bounds_world_m"] = [
                points_world.min(axis=0),
                points_world.max(axis=0),
            ]
            world_points.append(points_world)
        expected_occurrences.append(expected)

    physics_report = apply_physics(stage, records, package.occurrence_graph, config)
    # Assets live under defaultPrim so referencing the default asset includes physics.
    history_relative = history_dir.name + "/history.json"
    root.CreateAttribute("sca:history", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath(history_relative)
    )
    root.CreateAttribute("sca:package_content_hash", Sdf.ValueTypeNames.String).Set(
        package.content_hash
    )
    root.CreateAttribute("sca:history_semantics", Sdf.ValueTypeNames.String).Set(
        "Stored SCA operations and captured sources; reading does not execute CAD operations in USD."
    )
    root.CreateAttribute("sca:export_report", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath(report_path.name)
    )
    combined = np.concatenate(world_points)
    bounds_world = np.array([combined.min(axis=0), combined.max(axis=0)])
    report = dict(
        schema_version="1.0",
        input=str(input_path) if input_path else "in-memory ProductPackage",
        output=str(destination),
        history=history_relative,
        versions={
            name: importlib.metadata.version(name)
            for name in ("simplecadapi", "usd-core", "numpy")
        },
        configuration=config.to_dict(),
        from_sca=dict(
            root_definition_id=package.root_id,
            package_content_hash=package.content_hash,
            units="mm",
            definition_count=len(units),
            occurrence_graph=dict(package.occurrence_graph.manifest),
            placement_source="materialize_definition(root): verified solved snapshot or strict SCA solver",
        ),
        calculated=dict(
            length_scale=0.001,
            length_conversion="SCA mm -> USD m; vertices and local translations each converted once",
            bounds_world_m=bounds_world,
            dimensions_m=bounds_world[1] - bounds_world[0],
            part_occurrence_count=len(world_points),
            unique_part_definition_count=len(meshes),
            triangle_count=sum(
                e.get("triangle_count", 0) for e in expected_occurrences
            ),
            max_point_precision_error_m=max(
                m["precision_error_m"] for m in meshes.values()
            ),
        ),
        defaults=[
            dict(
                field="up_axis",
                component="stage",
                value=config.up_axis,
                unit="axis",
                reason="SCA canonical definitions have no global up-axis field; preserve CAD axes and label the configured up axis. The supplied dimensions.py explicitly uses +Y upward.",
            ),
            dict(
                field="linear_deflection_mm",
                component="all part definitions",
                value=config.linear_deflection_mm,
                unit="mm",
                reason="Configurable OCCT tessellation chord tolerance",
            ),
            dict(
                field="angular_deflection_degrees",
                component="all part definitions",
                value=config.angular_deflection_degrees,
                unit="degree",
                reason="Configurable OCCT tessellation angular tolerance",
            ),
            dict(
                field="relative_deflection",
                component="all part definitions",
                value=config.relative_deflection,
                unit="boolean",
                reason="Absolute chord tolerance is the demonstration default",
            ),
        ],
        occurrences=expected_occurrences,
        physics=physics_report,
        missing_or_unsupported=[
            "No Isaac Sim execution: contact, collision cooking and robot manipulation are unverified.",
            "No new hinge is inferred from geometry. Only explicit SCA revolute/prismatic relations can produce moving joints.",
            "Node IDs and source locations are preserved verbatim; automatic operation IDs are not guaranteed stable after inserting operations.",
            "History reading does not rebuild geometry. Parameter changes should rerun the original SCA notebook/builder and export again.",
        ],
        status=dict(
            file_written=False,
            geometry_validated=False,
            physics_static_validated=False,
            simulator_validated=False,
        ),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    history_path = write_history_bundle(
        package.root_definition, history_dir, package_content_hash=package.content_hash
    )
    history_manifest = read_history_bundle(history_path)
    report["history_summary"] = [
        {
            key: definition[key]
            for key in (
                "definition_id",
                "operation_count",
                "dependency_count",
                "source_files",
                "operations_missing_source",
            )
        }
        for definition in history_manifest["definitions"]
    ]
    report["defaults"].extend(physics_report["defaults"])
    provenance = []

    def fact(category, field, component, value, unit, reason):
        provenance.append(
            dict(
                category=category,
                field=field,
                component=component,
                value=value,
                unit=unit,
                reason=reason,
            )
        )

    fact(
        "from_sca",
        "length_unit",
        "all definitions",
        "mm",
        "unit",
        "PartDefinition.units / AssemblyDefinition.units",
    )
    for record in expected_occurrences:
        identifier = record["node_id"]
        fact(
            "from_sca",
            "identity_and_hierarchy",
            identifier,
            {
                key: record[key]
                for key in ("definition_id", "parent_node_id", "display_name")
            },
            "identity",
            "Validated ProductOccurrenceGraph and materialized component names",
        )
        fact(
            "calculated",
            "local_transform",
            identifier,
            record["local_matrix_m"],
            "m, orthonormal axes",
            "Materialized solved SCA placement; translation mm converted to m once",
        )
        if record["kind"] == "part":
            fact(
                "calculated",
                "triangle_count",
                identifier,
                record["triangle_count"],
                "triangle",
                "Per-definition OCCT tessellation, instanced per part occurrence",
            )
            fact(
                "calculated",
                "volume",
                identifier,
                record["volume_m3"],
                "m^3",
                "Evaluated OCCT BREP volume",
            )
    for group in physics_report["groups"]:
        for part in group["parts"]:
            if not part["density_source"].startswith("config."):
                fact(
                    "from_sca",
                    "density",
                    part["node_id"],
                    part["density_kg_m3"],
                    "kg/m^3",
                    part["density_source"],
                )
            fact(
                "calculated",
                "mass",
                part["node_id"],
                part["mass_kg"],
                "kg",
                "BREP volume times the reported density",
            )
        fact(
            "calculated",
            "rigid_group",
            group["group_id"],
            group["members"],
            "occurrence ID",
            "Union of explicit fixed relations and grounded assembly edges",
        )
        fact(
            "calculated",
            "inertia",
            group["group_id"],
            group["inertia_tensor_body_kg_m2"],
            "kg m^2",
            "OCCT central inertia with explicit IFlag; rigid rotation and parallel-axis aggregation",
        )
    for entry in report["defaults"]:
        fact(
            "default_or_explicit_configuration",
            entry["field"],
            entry.get(
                "node_id",
                entry.get(
                    "group_id", entry.get("joint_id", entry.get("component", "stage"))
                ),
            ),
            entry["value"],
            entry["unit"],
            entry["reason"],
        )
    for entry in physics_report["unsupported"]:
        fact(
            "missing_or_unsupported",
            "constraint_mapping",
            entry.get("joint_id", "model"),
            entry,
            "relation",
            entry["reason"],
        )
    for entry in report["history_summary"]:
        if entry["operations_missing_source"]:
            fact(
                "missing_or_unsupported",
                "operation_source_location",
                entry["definition_id"],
                entry["operations_missing_source"],
                "node ID",
                "Original graph has no captured source for these nodes",
            )
    fact(
        "missing_or_unsupported",
        "simulator_validation",
        "stage",
        False,
        "boolean",
        "Isaac Sim was not run",
    )
    if not package.occurrence_graph.joints or all(
        joint["joint_type"] == "fixed" for joint in package.occurrence_graph.joints
    ):
        fact(
            "missing_or_unsupported",
            "movable_joint",
            "model",
            None,
            "relation",
            "Input contains no explicit revolute or prismatic joint; none inferred",
        )
    report["data_provenance"] = provenance
    stage.GetRootLayer().Export(str(destination))
    report["status"]["file_written"] = destination.is_file()
    # Reopen the exported file, not the in-memory stage, and compare with SCA geometry.
    report["validation"] = validate_usd(destination, expected=report)
    report["status"]["geometry_validated"] = report["validation"]["ok"]
    report["status"]["physics_static_validated"] = report["validation"][
        "physics_static_ok"
    ]
    write_new_json(report_path, report)
    return report
