"""SCA package -> local definition meshes, semantic instances and USD Physics."""

import importlib.metadata
import hashlib
import json
import math
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


def _geometry_tolerance_policy(package, config, explicit_fields):
    """Use only a validated, saved export recommendation; never infer physics."""
    recommendations = {}
    embedded = package.root_definition.metadata.get("sca_physics")
    for identity, item in (embedded or {}).get("cables", {}).items():
        resolution = item.get("resolution", {})
        solver = resolution.get("solver", {}) if isinstance(resolution, dict) else {}
        if "export_geometry_absolute_tolerance_m" in solver:
            suggestion = solver["export_geometry_absolute_tolerance_m"]
            if isinstance(suggestion, bool) or not isinstance(suggestion, (float, int)) or not math.isfinite(suggestion) or suggestion <= 0:
                raise ValueError("Saved export geometry tolerance must be positive and finite")
            recommendations[identity] = float(suggestion)
    if recommendations:
        from .dependencies import require_physics
        require_physics()
        from sca_physics.contract import validate_config
        validate_config(package, embedded)
    explicit = "geometry_absolute_tolerance_m" in explicit_fields
    selected = config.geometry_absolute_tolerance_m
    if recommendations and not explicit:
        selected = max(selected, *recommendations.values())
    return {"value": selected, "unit": "m", "origin": (
                "explicit_export_configuration" if explicit else
                "saved_cable_resolution" if recommendations else "centralized_export_default"),
            "configured_value_before_saved_recommendation_m": config.geometry_absolute_tolerance_m,
            "saved_recommendations_m": recommendations,
            "purpose": "CAD Mesh float32 geometric rounding guard only; no change to material or native mechanics acceptance"}


def export_package(data, output_path, *, config=None, up_axis_override=None, explicit_config_fields=None,
                   expected_input_sha256=None, require_physics=False):
    """Export one validated ProductPackage (or .scadpkg path) to a new .usda.

    Output paths are never overwritten. All coordinates are authored in metres,
    retaining CAD axis directions and the materialized, solved local transforms.
    The semantic product tree and physics grouping are linked explicitly.
    """
    supplied_config = config is not None
    config = config or ExportConfig()
    if not isinstance(config, ExportConfig):
        raise ValueError("config must be ExportConfig")
    # CLI supplies exact JSON keys (including an empty set). For tolerance only,
    # a direct API config object gets conservative precedence, even for a value
    # deliberately equal to the default. Existing physics override rules retain
    # their previous nondefault-field detection and are not widened here.
    tolerance_explicit_fields = ({"geometry_absolute_tolerance_m"} if supplied_config else set()) if explicit_config_fields is None else set(explicit_config_fields)
    explicit_config_fields = set(explicit_config_fields or ())
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
    # Decode exactly the bytes whose identity was checked. In particular, do not
    # validate one file and let the reader reopen another same-named revision.
    if input_path is not None:
        data = input_path.read_bytes()
    input_sha256 = hashlib.sha256(data).hexdigest() if isinstance(data, (bytes, bytearray, memoryview)) else None
    if expected_input_sha256 is not None:
        if (not isinstance(expected_input_sha256, str) or len(expected_input_sha256) != 64
                or any(c not in '0123456789abcdefABCDEF' for c in expected_input_sha256)):
            raise ValueError('expected_input_sha256 must be a 64-character SHA256 hex digest')
        if input_sha256 is None or input_sha256 != expected_input_sha256.lower():
            raise ValueError('Input package SHA256 differs from the selected final package; no export written')
    if type(require_physics) is not bool:
        raise ValueError('require_physics must be boolean')
    Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt = require_usd()
    from simplecadapi.translator.package_units import (
        read_product_package_translation_units,
    )
    from .physics import apply_physics
    from .validation import validate_usd

    package, units = read_product_package_translation_units(data)
    embedded = package.root_definition.metadata.get('sca_physics')
    if require_physics and embedded is None:
        raise ValueError('Selected final package has no metadata.sca_physics; refusing raw CAD export')
    physics_sha256 = hashlib.sha256(json.dumps(embedded, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False, default=_json_default).encode('utf-8')).hexdigest() if embedded is not None else None
    if any(unit.definition.units != "mm" for unit in units):
        raise ValueError("this extension requires SCA canonical millimetre definitions")
    from dataclasses import replace
    geometry_tolerance = _geometry_tolerance_policy(package, config, tolerance_explicit_fields)
    config = replace(config, geometry_absolute_tolerance_m=geometry_tolerance["value"])
    records, meshes = collect_model(package, config)
    from .embedded_physics import package_settings
    nondefault_physics = {field for field in (
        "up_axis", "default_friction", "default_dynamic_friction", "default_restitution",
        "dynamic_collision_approximation", "static_collision_approximation", "joint_collision_enabled"
    ) if getattr(config, field) != getattr(ExportConfig(), field)}
    physics_settings, physics_input = package_settings(
        package, records, config, up_axis_override=up_axis_override,
        explicit_config_fields=set(explicit_config_fields) | nondefault_physics,
    )
    config = replace(config, up_axis=physics_settings["scene"]["up_axis"]["value"][1:])
    from .rigid_joint_chain import chain_declarations, scene_settings
    rigid_chains = chain_declarations(physics_settings)
    fem_cables = {key: item for key, item in physics_settings.get('cables', {}).items()
                 if item.get('representation') == 'physx_deformable_volume'}
    unknown = {item.get('representation') for item in physics_settings.get('cables', {}).values()} - {'physx_deformable_volume', 'rigid_joint_chain'}
    if unknown:
        raise ValueError(f'Unsupported cable representation: {sorted(map(str, unknown))}')
    if rigid_chains and fem_cables:
        raise ValueError('CPU rigid_joint_chain and GPU native FEM scene policies conflict; export separate scenes explicitly')
    if rigid_chains:
        config = replace(config, gravity_m_s2=scene_settings(rigid_chains)['gravity_magnitude'])

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

    physics_report = apply_physics(stage, records, package.occurrence_graph, config, physics_settings)
    if fem_cables:
        from .native_cables import author_cables
        physics_report["cables"] = author_cables(stage, fem_cables, records, physics_report,
                                                 package=package, meshes=meshes, config=config)
        scene_prim = stage.GetPrimAtPath(physics_report["scene"]["prim_path"])
        scene_prim.AddAppliedSchema("PhysxSceneAPI")
        for name, kind, value in (("enableGPUDynamics", "Bool", True), ("broadphaseType", "Token", "GPU"), ("solverType", "Token", "TGS"), ("timeStepsPerSecond", "UInt", 480)):
            scene_prim.CreateAttribute("physxScene:" + name, getattr(Sdf.ValueTypeNames, kind), custom=False).Set(value)
        physics_report["native_deformable_requirements"] = {
            "backend": "Omni PhysX 107.3.18", "gpu_dynamics": True,
            "runtime_setting": "/persistent/physics/enableDeformableBeta", "runtime_value": True,
            "setting_scope": "Simulator session; exporter does not alter persistent settings",
            "material_semantics": "Native finite-element density, Young modulus and Poisson ratio; no joint surrogate",
            "time_steps_per_second": {"value":480,"unit":"Hz","usd_attribute":"physxScene:timeStepsPerSecond","usd_type":"uint",
                "schema_fallback":60,"source":{"kind":"default","detail":"Case-specific stable-step assumption: actual native cable acceptance and viewer use 1/480 s; no timestep convergence calibration has been performed"}},
        }
    if rigid_chains:
        from .rigid_joint_chain import author_rigid_joint_chains
        physics_report['rigid_chains'] = author_rigid_joint_chains(stage, rigid_chains, records, physics_report)
    physics_report["input_configuration"] = physics_input
    physics_report["warnings"].extend(physics_input["warnings"])
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
    cable_bounds = [np.asarray(cable["bounds_world_m"]) for cable in physics_report.get("cables",[]) + physics_report.get('rigid_chains', [])]
    scene_bounds_world = np.asarray([np.vstack([bounds_world[0]]+[bounds[0] for bounds in cable_bounds]).min(axis=0),
                                     np.vstack([bounds_world[1]]+[bounds[1] for bounds in cable_bounds]).max(axis=0)])
    report = dict(
        schema_version="1.0",
        input=str(input_path) if input_path else "in-memory ProductPackage",
        input_package_sha256=input_sha256,
        physics_metadata_sha256=physics_sha256,
        input_require_physics=require_physics,
        output=str(destination),
        history=history_relative,
        versions={
            name: importlib.metadata.version(name)
            for name in ("simplecadapi", "usd-core", "numpy")
        },
        configuration=config.to_dict(),
        geometry_tolerance_policy=geometry_tolerance,
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
            bounds_world_scope="SCA CAD geometry only; preserved legacy field. Complete initial asset bounds are scene_bounds_world_m.",
            cad_bounds_world_m=bounds_world,
            cad_dimensions_m=bounds_world[1]-bounds_world[0],
            scene_bounds_world_m=scene_bounds_world,
            scene_dimensions_m=scene_bounds_world[1]-scene_bounds_world[0],
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
                reason="Resolved package sca_physics scene.up_axis, explicit CLI override, or traced legacy +Z policy. Labels USD upAxis and sets gravity; geometry coordinates remain unchanged.",
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
    if input_path is not None and hashlib.sha256(input_path.read_bytes()).hexdigest() != input_sha256:
        raise ValueError('Input package changed during export; no output written')
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
    from .stage_units import convert_stage_units
    report['stage_units'] = convert_stage_units(stage, config.meters_per_unit)
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
