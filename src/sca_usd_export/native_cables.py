"""Author the local Omni PhysX 107.3 native volume deformable schema.

The portable usd-core runtime stores schema tokens/properties explicitly. The
actual PhysX plugins and beta feature are supplied by the simulator, not bundled
into this add-on. Reopening USD proves authored data, never solver behaviour.
"""

from copy import deepcopy
import hashlib
import json
import math
import numpy as np

from .cable_mesh import swept_tetrahedra
from .mechanical_properties import applied_schema_tokens

MATERIAL = {
    "density": ("OmniPhysicsDeformableMaterialAPI", "omniphysics:density"),
    "youngs_modulus": ("OmniPhysicsDeformableMaterialAPI", "omniphysics:youngsModulus"),
    "poissons_ratio": ("OmniPhysicsDeformableMaterialAPI", "omniphysics:poissonsRatio"),
    "dynamic_friction": ("OmniPhysicsDeformableMaterialAPI", "omniphysics:dynamicFriction"),
    "elasticity_damping": ("PhysxDeformableMaterialAPI", "physxDeformableMaterial:elasticityDamping"),
}
BODY = {
    "gravity_enabled": ("physxDeformableBody:disableGravity", "Bool"),
    "linear_damping": ("physxDeformableBody:linearDamping", "Float"),
    "self_collision": ("physxDeformableBody:selfCollision", "Bool"),
    "solver_position_iterations": ("physxDeformableBody:solverPositionIterationCount", "UInt"),
    "enable_speculative_ccd": ("physxDeformableBody:enableSpeculativeCCD", "Bool"),
    'sleep_threshold': ('physxDeformableBody:sleepThreshold', 'Float'),
    'settling_threshold': ('physxDeformableBody:settlingThreshold', 'Float'),
    'settling_damping': ('physxDeformableBody:settlingDamping', 'Float'),
}


def _digest(points, tets, surface):
    digest = hashlib.sha256()
    for array, dtype in ((points, '<f4'), (tets, '<i4'), (surface, '<i4')):
        digest.update(np.asarray(array, dtype=dtype).tobytes())
    return digest.hexdigest()


def author_cables(stage, declarations, records, physics_report, *, package=None, meshes=None, config=None):
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics, UsdShade
    if not declarations:
        return []
    by_node = {record["node_id"]: record for record in records}
    groups = {member: group for group in physics_report["groups"] for member in group["members"]}
    output = []
    cable_scope = physics_report["physics_root"] + "/Cables"
    UsdGeom.Scope.Define(stage, cable_scope)
    for cable_id, declaration in sorted(declarations.items()):
        target_node_id = declaration.get('target_node_id')
        source_surface = None
        target_path = None
        if target_node_id is not None:
            if package is None or meshes is None or config is None:
                raise ValueError('CAD deformable export requires the actual package, collected meshes and export config')
            from .deformable_targets import prepare_from_records
            source_surface = prepare_from_records(package, target_node_id, records, meshes, config)
            generated_input = declaration['simulation_mesh'].get('generated', {})
            if generated_input.get('source_geometry_sha256') != source_surface['source_geometry_sha256']:
                raise ValueError(f'{target_node_id}: generated mesh source surface fingerprint is stale; regenerate the official mesh and endpoint indices for the current CAD/tessellation')
            target_path = by_node[target_node_id]['prim_path']
        path = cable_scope + "/Cable_" + hashlib.sha256(cable_id.encode()).hexdigest()[:20]
        root = UsdGeom.Xform.Define(stage, path).GetPrim()
        root.SetDisplayName(cable_id)
        root.AddAppliedSchema("OmniPhysicsDeformableBodyAPI")
        root.AddAppliedSchema("PhysxBaseDeformableBodyAPI")
        root.CreateAttribute("sca:cable_id", Sdf.ValueTypeNames.String).Set(cable_id)
        if target_path:
            root.CreateAttribute('sca:target_node_id', Sdf.ValueTypeNames.String).Set(target_node_id)
            root.CreateAttribute('sca:physical_type', Sdf.ValueTypeNames.Token).Set('deformable_volume')
            root.CreateRelationship('sca:sourceOccurrence').SetTargets([Sdf.Path(target_path)])
            root.CreateAttribute('sca:geometrySource', Sdf.ValueTypeNames.String).Set(json.dumps(source_surface['geometry_source'], sort_keys=True))
            semantic = stage.GetPrimAtPath(target_path)
            semantic.CreateRelationship('sca:physicsBody').SetTargets([Sdf.Path(path)])
            semantic.CreateRelationship('sca:physicsGeometry').SetTargets([Sdf.Path(path + '/SimulationMesh')])
            semantic.CreateRelationship('sca:renderGeometry').SetTargets([Sdf.Path(path + '/RenderMesh')])
            semantic.CreateAttribute('sca:physical_type', Sdf.ValueTypeNames.Token).Set('deformable_volume')
            UsdGeom.Imageable(semantic).CreateVisibilityAttr('invisible')
        parent_path = by_node[declaration["parent_node_id"]]["prim_path"]
        root.CreateRelationship("sca:parentOccurrence").SetTargets([Sdf.Path(parent_path)])
        stage.GetPrimAtPath(parent_path).CreateRelationship("sca:deformableCables").AddTarget(Sdf.Path(path))
        root.CreateAttribute("sca:physicsDeclaration", Sdf.ValueTypeNames.String).Set(json.dumps(declaration, sort_keys=True, ensure_ascii=False))
        authored = []
        def attribute(prim, name, kind, value, *, parameter=None):
            prop = prim.CreateAttribute(name, getattr(Sdf.ValueTypeNames, kind), custom=False)
            if not prop.Set(value):
                raise ValueError("Could not author native deformable property: " + name)
            authored.append({"prim_path": str(prim.GetPath()), "attribute": name, "type": str(prop.GetTypeName()),
                             "value": value, "input": deepcopy(parameter)})
        geometry, mesh_spec = declaration["geometry"], declaration["simulation_mesh"]
        official = mesh_spec["method"] == "physx_conforming_tetrahedra"
        if official:
            from sca_physics.official_mesh import validate_generated
            generated = validate_generated(mesh_spec["generated"], geometry, "simulation_mesh.generated",
                                           cad_source=declaration.get('geometry_source'))
            points = np.asarray(generated["points"],dtype=np.float32)
            tets = np.asarray(generated["tets"],dtype=np.int32)
            surface = np.asarray(generated["surface"],dtype=np.int32)
            ends = deepcopy(generated["ends"])
        else:
            points, tets, surface, ends = swept_tetrahedra(geometry["centerline"]["value"], geometry["diameter"]["value"],
                mesh_spec["axial_segments"]["value"], mesh_spec["radial_segments"]["value"])
        tet = UsdGeom.TetMesh.Define(stage, path + "/SimulationMesh")
        tet.CreatePointsAttr([Gf.Vec3f(*map(float,p)) for p in points])
        tet.CreateTetVertexIndicesAttr([Gf.Vec4i(*map(int,t)) for t in tets])
        tet.CreateSurfaceFaceVertexIndicesAttr([Gf.Vec3i(*map(int,f)) for f in surface])
        tet.CreateExtentAttr([Gf.Vec3f(*map(float,points.min(axis=0))), Gf.Vec3f(*map(float,points.max(axis=0)))])
        tet.CreateDisplayColorAttr([Gf.Vec3f(.055,.06,.065)])
        tet.GetPrim().AddAppliedSchema("OmniPhysicsVolumeDeformableSimAPI")
        tet.GetPrim().CreateAttribute("omniphysics:restShapePoints", Sdf.ValueTypeNames.Point3fArray, custom=False).Set(tet.GetPointsAttr().Get())
        tet.GetPrim().CreateAttribute("omniphysics:restTetVtxIndices", Sdf.ValueTypeNames.Int4Array, custom=False).Set(tet.GetTetVertexIndicesAttr().Get())
        # TetMesh is the sole solver/collision mesh. Hydra needs a surface Mesh;
        # the native pose API embeds it and updates its vertices automatically.
        skin = UsdGeom.Mesh.Define(stage, path + "/RenderMesh")
        skin_points = np.asarray(source_surface['points'], dtype=np.float32) if source_surface else points
        skin_faces = np.asarray(source_surface['triangles'], dtype=np.int32) if source_surface else surface
        skin.CreatePointsAttr([Gf.Vec3f(*map(float,p)) for p in skin_points])
        skin.CreateFaceVertexCountsAttr([3]*len(skin_faces))
        skin.CreateFaceVertexIndicesAttr([int(vertex) for face in skin_faces for vertex in face])
        skin.CreateSubdivisionSchemeAttr("none")
        skin.CreateOrientationAttr("rightHanded")
        skin.CreateDoubleSidedAttr(True)
        skin.CreateDisplayColorAttr([Gf.Vec3f(.055,.06,.065)])
        skin.CreateExtentAttr([Gf.Vec3f(*map(float,skin_points.min(axis=0))), Gf.Vec3f(*map(float,skin_points.max(axis=0)))])
        skin.CreatePurposeAttr("default")
        for prim in (tet.GetPrim(), skin.GetPrim()):
            prim.AddAppliedSchema("OmniPhysicsDeformablePoseAPI:custom")
            prim.CreateAttribute("deformablePose:custom:omniphysics:purposes", Sdf.ValueTypeNames.TokenArray, custom=False, variability=Sdf.VariabilityUniform).Set(["bindPose"])
            prim.CreateAttribute("deformablePose:custom:omniphysics:points", Sdf.ValueTypeNames.Point3fArray, custom=False).Set(prim.GetAttribute('points').Get())
        tet.CreatePurposeAttr("guide")
        collision = declaration["collision"]
        UsdPhysics.CollisionAPI.Apply(tet.GetPrim()).CreateCollisionEnabledAttr(collision["enabled"]["value"])
        attribute(tet.GetPrim(), "physics:collisionEnabled", "Bool", collision["enabled"]["value"], parameter=collision["enabled"])
        tet.GetPrim().AddAppliedSchema("PhysxCollisionAPI")
        for field, name in (("contact_offset", "contactOffset"), ("rest_offset", "restOffset")):
            attribute(tet.GetPrim(), "physxCollision:"+name, "Float", collision[field]["value"], parameter=collision[field])
        for field, parameter in declaration.get("deformable", {}).items():
            name, kind = BODY[field]
            value = not parameter["value"] if field == "gravity_enabled" else parameter["value"]
            attribute(root, name, kind, value, parameter=parameter)
        material = UsdShade.Material.Define(stage, path + "/Material")
        for field, parameter in declaration["material"].items():
            api, name = MATERIAL[field]
            material.GetPrim().AddAppliedSchema(api)
            attribute(material.GetPrim(), name, "Float", parameter["value"], parameter=parameter)
        UsdShade.MaterialBindingAPI.Apply(root).Bind(material, UsdShade.Tokens.weakerThanDescendants, "physics")
        if mesh_spec.get('generated', {}).get('centerline_embedding') is not None:
            root.CreateAttribute('sca:centerlineEmbedding', Sdf.ValueTypeNames.String).Set(json.dumps(mesh_spec['generated']['centerline_embedding'], sort_keys=True))
        attachments = []
        filtering_input=declaration["collision"].get("attachment_filtering")
        filtering_scope=filtering_input["value"] if filtering_input is not None else "whole_body"
        element_filters=[]
        for declaration_attachment in declaration["attachments"]:
            node_id = declaration_attachment["rigid_node_id"]
            group = groups[node_id]
            mode = group.get("mode", "dynamic" if group["dynamic"] else "static")
            if mode not in {"dynamic", "kinematic"}:
                raise ValueError(f"Cable {cable_id}: endpoint {node_id} resolves to a static group; endpoint requires a rigid actor")
            component = by_node[node_id]
            endpoint = declaration_attachment["endpoint"]
            local_position = np.asarray(declaration_attachment["local_frame"]["position"]["value"])
            expected_world = np.asarray(component["world_matrix"]) @ np.r_[local_position, 1.]
            endpoint_world = np.asarray(geometry["centerline"]["value"][0 if endpoint == "start" else -1])
            if not np.allclose(expected_world[:3], endpoint_world, rtol=0, atol=2e-6):
                raise ValueError(f"Cable {cable_id}.{endpoint}: local_frame.position differs from centreline endpoint in the solved SCA occurrence")
            attach_path = path + "/Attachment_" + endpoint
            attach = stage.DefinePrim(attach_path, "OmniPhysicsVtxXformAttachment")
            attach.CreateRelationship("omniphysics:src0", custom=False).SetTargets([tet.GetPath()])
            attach.CreateRelationship("omniphysics:src1", custom=False).SetTargets([Sdf.Path(group["body_path"])])
            indices = ends[endpoint]
            body_matrix = np.asarray(UsdGeom.Xformable(stage.GetPrimAtPath(group["body_path"])).ComputeLocalToWorldTransform(0)).T
            inverse = np.linalg.inv(body_matrix)
            local_points = (inverse @ np.c_[points[indices], np.ones(len(indices))].T).T[:,:3]
            attach.CreateAttribute("omniphysics:vtxIndicesSrc0", Sdf.ValueTypeNames.IntArray, custom=False).Set(indices)
            attach.CreateAttribute("omniphysics:localPositionsSrc1", Sdf.ValueTypeNames.Point3fArray, custom=False).Set([Gf.Vec3f(*map(float,p)) for p in local_points])
            if filtering_scope == "whole_body":
                UsdPhysics.FilteredPairsAPI.Apply(tet.GetPrim()).CreateFilteredPairsRel().AddTarget(Sdf.Path(group["body_path"]))
            else:
                from .cable_collision_filters import endpoint_surface_indices, physical_endpoint_surface_indices, author_endpoint_filters
                if official:
                    face_indices=physical_endpoint_surface_indices(generated["reference_points"],surface,
                        geometry["centerline"]["value"],mesh_spec["filter_length"]["value"],endpoint)
                else:
                    face_indices=endpoint_surface_indices(surface,mesh_spec["radial_segments"]["value"],mesh_spec["axial_segments"]["value"],endpoint)
                element_filters.extend(author_endpoint_filters(stage,path,str(tet.GetPath()),group,endpoint,face_indices))
            attachments.append({"prim_path": attach_path, "endpoint": endpoint, "rigid_node_id": node_id,
                "rigid_body_path": group["body_path"], "rigid_body_mode": mode, "vertex_indices": indices,
                "local_positions_m": local_points.tolist(), "input": deepcopy(declaration_attachment),
                "constraint": "native two-way vertex/xform attachment; entire endpoint cross-section",
                "vertex_selection_source":"actual_generated_endpoint_cross_section" if official else "swept_endpoint_ring"})
        tet_volume = sum(np.linalg.det((points[t[1:]]-points[t[0]]).astype(float))/6 for t in tets)
        ideal_volume = math.pi * (geometry["diameter"]["value"]/2)**2 * geometry["length"]["value"]
        output.append({"cable_id": cable_id, "prim_path": path, "simulation_mesh_path": str(tet.GetPath()),
            'target_node_id': target_node_id, 'target_occurrence_path': target_path,
            'geometry_source': deepcopy(source_surface['geometry_source']) if source_surface else None,
            'source_geometry_sha256': source_surface['source_geometry_sha256'] if source_surface else None,
            'render_geometry_sha256': __import__('sca_usd_export.model', fromlist=['geometry_digest']).geometry_digest(skin_points, skin_faces),
            'render_vertex_count': len(skin_points), 'render_triangle_count': len(skin_faces),
            'render_geometry_source': 'actual_selected_cad_occurrence_closed_surface' if source_surface else 'tetrahedron_boundary',
            'mass_precedence': 'Deformable material density; no CAD/rigid MassAPI authored',
            "render_mesh_path":str(skin.GetPath()), "render_skin":"Native OmniPhysicsDeformablePoseAPI:custom bindPose embedding; backend updates surface vertices without frame scripts; no collider/rigid actor/mass on skin",
            "bounds_world_m":[points.min(axis=0).tolist(),points.max(axis=0).tolist()],
            "parent_occurrence_path": parent_path, "material_path": str(material.GetPath()), "declaration": deepcopy(declaration),
            "vertex_count":len(points), "tetrahedron_count":len(tets), "surface_triangle_count":len(surface),
            "mesh_sha256":_digest(points,tets,surface), "end_rings": ends, "attachments": attachments,
            "mesh_generation":{"method":mesh_spec["method"],"source":"persisted_official_surface_to_volume_output" if official else "exporter_swept_tetrahedra",
                "provenance":deepcopy(generated["provenance"]) if official else None,
                "surface_input_discretization":{"axial_segments":mesh_spec["axial_segments"]["value"],"radial_segments":mesh_spec["radial_segments"]["value"]} if official else None},
            "mesh_volume_m3":float(tet_volume), "mesh_mass_kg":float(tet_volume*declaration["material"]["density"]["value"]),
            "ideal_circular_volume_m3":ideal_volume, "mesh_volume_relative_error":float(tet_volume/ideal_volume-1),
            "collision_filtering":("Entire cable is filtered against its two attached rigid plug groups; historical whole_body semantics retained" if filtering_scope=="whole_body" else ("Only surface triangles whose reference vertices project within the explicitly persisted physical endpoint length are filtered against their attached plug's actual colliders" if official else "Only endpoint cap and first axial-span surface triangles are filtered against their attached plug's actual colliders; remaining cable/plug contact remains active")),
            "attachment_filtering":{"effective_scope":filtering_scope,"input":deepcopy(filtering_input),
                "origin":"explicit_package_parameter" if filtering_input is not None else "historical_omitted_field_compatibility",
                "selection_policy":(("reference_projection_physical_length" if official else "cap_plus_first_axial_span") if filtering_scope=="endpoint_elements" else "whole_cable_vs_attached_rigid_groups"),
                "endpoint_axial_span_count":1 if filtering_scope=="endpoint_elements" and not official else None,
                "endpoint_span_length_m":(mesh_spec["filter_length"]["value"] if official else geometry["length"]["value"]/mesh_spec["axial_segments"]["value"]) if filtering_scope=="endpoint_elements" else None,
                "physical_length_input":deepcopy(mesh_spec["filter_length"]) if official else None,
                "element_index_source":"Rows of actual SimulationMesh.surfaceFaceVertexIndices selected from persisted reference positions" if official else "Rows of SimulationMesh.surfaceFaceVertexIndices; fixed generated topology policy, not material or attachment compliance"},
            "element_collision_filters":element_filters,
            "attachment_frame_semantics":"Origin checked against solved component-local frame; native per-vertex positions in rigid body coordinates define the full cross-section attachment. Quaternion is retained as port-frame provenance; native attachment has no separate quaternion opinion.",
            "authored_properties": authored, "representation":"physx_deformable_volume", "solver":"Omni PhysX native finite elements",
            "target":"Isaac Sim 5.0 / Omni PhysX 107.3.18; /persistent/physics/enableDeformableBeta=true; GPU dynamics required by backend",
            "simulation_verified":False})
    return output


def validate_cables(stage, records, tolerance=2e-6, physics_report=None):
    from pxr import UsdGeom, UsdPhysics, UsdShade
    if records and physics_report is not None:
        scene = stage.GetPrimAtPath(physics_report["scene"]["prim_path"])
        if not scene or "PhysxSceneAPI" not in applied_schema_tokens(scene):
            raise ValueError("Native deformable GPU scene API missing")
        for name, kind, value in (("enableGPUDynamics", "bool", True), ("broadphaseType", "token", "GPU"), ("solverType", "token", "TGS"), ("timeStepsPerSecond", "uint", 480)):
            attr = scene.GetAttribute("physxScene:"+name)
            if not attr or attr.Get() != value or str(attr.GetTypeName()) != kind or attr.IsCustom() or not attr.HasAuthoredValueOpinion():
                raise ValueError("Native deformable scene property changed: " + name)
    for record in records:
        root = stage.GetPrimAtPath(record["prim_path"])
        if not {"OmniPhysicsDeformableBodyAPI", "PhysxBaseDeformableBodyAPI"} <= applied_schema_tokens(root):
            raise ValueError("Native deformable root schemas changed")
        if root.GetAttribute("sca:cable_id").Get() != record["cable_id"]:
            raise ValueError("Cable identity changed")
        if record.get('target_node_id'):
            target = stage.GetPrimAtPath(record['target_occurrence_path'])
            if not target or target.GetAttribute('sca:node_id').Get() != record['target_node_id']:
                raise ValueError('CAD deformable target occurrence identity changed')
            if root.GetAttribute('sca:target_node_id').Get() != record['target_node_id'] or root.GetAttribute('sca:physical_type').Get() != 'deformable_volume':
                raise ValueError('CAD deformable type/target changed')
            if [str(p) for p in root.GetRelationship('sca:sourceOccurrence').GetTargets()] != [record['target_occurrence_path']]:
                raise ValueError('CAD deformable source occurrence link changed')
            if [str(p) for p in target.GetRelationship('sca:physicsBody').GetTargets()] != [record['prim_path']]:
                raise ValueError('CAD deformable semantic body backlink changed')
            if json.loads(root.GetAttribute('sca:geometrySource').Get()) != record['geometry_source']:
                raise ValueError('CAD deformable geometry source snapshot changed')
            ancestor = target
            while ancestor and not ancestor.IsPseudoRoot():
                if ancestor.HasAPI(UsdPhysics.RigidBodyAPI) or ancestor.HasAPI(UsdPhysics.MassAPI) or ancestor.HasAPI(UsdPhysics.CollisionAPI):
                    raise ValueError('CAD deformable semantic occurrence has residual rigid/mass/collision API')
                ancestor = ancestor.GetParent()
            if any(record['target_node_id'] in group['members'] for group in (physics_report or {}).get('groups', [])):
                raise ValueError('CAD deformable target remains in a rigid group')
        if not np.allclose(UsdGeom.Xformable(root).ComputeLocalToWorldTransform(0),np.eye(4),rtol=0,atol=tolerance):
            raise ValueError("Cable world-space simulation root transform changed")
        if [str(p) for p in root.GetRelationship("sca:parentOccurrence").GetTargets()] != [record["parent_occurrence_path"]]:
            raise ValueError("Cable parent occurrence relationship changed")
        parent = stage.GetPrimAtPath(record["parent_occurrence_path"])
        if not parent or record["prim_path"] not in [str(p) for p in parent.GetRelationship("sca:deformableCables").GetTargets()]:
            raise ValueError("Cable semantic parent backlink changed")
        if json.loads(root.GetAttribute("sca:physicsDeclaration").Get()) != record["declaration"]:
            raise ValueError("Cable declaration/provenance changed")
        tet = UsdGeom.TetMesh(stage.GetPrimAtPath(record["simulation_mesh_path"]))
        if not tet or "OmniPhysicsVolumeDeformableSimAPI" not in applied_schema_tokens(tet.GetPrim()):
            raise ValueError("Native volume simulation mesh schema missing")
        if not np.allclose(UsdGeom.Xformable(tet.GetPrim()).ComputeLocalToWorldTransform(0),np.eye(4),rtol=0,atol=tolerance):
            raise ValueError("Native world-space simulation mesh transform changed")
        if not tet.GetPrim().HasAPI(UsdPhysics.CollisionAPI) or "PhysxCollisionAPI" not in applied_schema_tokens(tet.GetPrim()):
            raise ValueError("Native deformable collision APIs missing")
        if sum(p.HasAPI(UsdPhysics.CollisionAPI) for p in stage.Traverse() if str(p.GetPath()).startswith(record["prim_path"]+"/")) != 1:
            raise ValueError("Native deformable requires exactly one collider")
        points, tets, surface = [np.asarray(attr.Get()) for attr in (tet.GetPointsAttr(),tet.GetTetVertexIndicesAttr(),tet.GetSurfaceFaceVertexIndicesAttr())]
        mesh_spec=record["declaration"]["simulation_mesh"]
        official=mesh_spec["method"]=="physx_conforming_tetrahedra"
        if official:
            from sca_physics.official_mesh import validate_generated
            generated=validate_generated(mesh_spec["generated"],record["declaration"]["geometry"],"simulation_mesh.generated",
                                         cad_source=record['declaration'].get('geometry_source'))
            if (not np.array_equal(points,np.asarray(generated["points"],dtype=np.float32))
                    or not np.array_equal(tets,np.asarray(generated["tets"],dtype=np.int32))
                    or not np.array_equal(surface,np.asarray(generated["surface"],dtype=np.int32))
                    or record["end_rings"]!=generated["ends"]):
                raise ValueError("Official generated mesh/reference or endpoint selection changed")
            mesh_generation=record.get("mesh_generation",{})
            if (mesh_generation.get("method")!=mesh_spec["method"]
                    or mesh_generation.get("source")!="persisted_official_surface_to_volume_output"
                    or mesh_generation.get("provenance")!=generated["provenance"]):
                raise ValueError("Official mesher provenance report changed")
        if _digest(points,tets,surface) != record["mesh_sha256"]:
            raise ValueError("Cable tetrahedral mesh changed")
        if not np.array_equal(points,np.asarray(tet.GetPrim().GetAttribute("omniphysics:restShapePoints").Get())) or not np.array_equal(tets,np.asarray(tet.GetPrim().GetAttribute("omniphysics:restTetVtxIndices").Get())):
            raise ValueError("Cable native rest mesh changed")
        if "render_mesh_path" in record:
            skin = UsdGeom.Mesh(stage.GetPrimAtPath(record["render_mesh_path"]))
            if not skin or skin.GetPrim().HasAPI(UsdPhysics.CollisionAPI) or skin.GetPrim().HasAPI(UsdPhysics.RigidBodyAPI) or skin.GetPrim().HasAPI(UsdPhysics.MassAPI):
                raise ValueError("Native render skin missing or has unintended solver/collision/mass APIs")
            skin_points = np.asarray(skin.GetPointsAttr().Get())
            skin_faces = np.asarray(skin.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
            from .model import geometry_digest
            if record.get('render_geometry_sha256'):
                if geometry_digest(skin_points, skin_faces) != record['render_geometry_sha256'] or len(skin_points) != record['render_vertex_count'] or len(skin_faces) != record['render_triangle_count']:
                    raise ValueError('Native actual CAD render skin points/topology changed')
            elif not np.array_equal(points,skin_points) or not np.array_equal(surface.reshape(-1),skin_faces.reshape(-1)):
                raise ValueError('Native render skin points/topology changed')
            if not np.array_equal(np.full(len(skin_faces), 3), np.asarray(skin.GetFaceVertexCountsAttr().Get())):
                raise ValueError('Native render skin non-triangle counts changed')
            if tet.GetPurposeAttr().Get() != "guide" or skin.GetPurposeAttr().Get() != "default":
                raise ValueError("Native simulation/render purpose changed")
            if not np.allclose(UsdGeom.Xformable(skin.GetPrim()).ComputeLocalToWorldTransform(0),np.eye(4),rtol=0,atol=tolerance):
                raise ValueError("Native render skin transform changed")
            if UsdGeom.Imageable(skin.GetPrim()).ComputeVisibility() == "invisible":
                raise ValueError("Native render skin is invisible")
            for prim in (tet.GetPrim(),skin.GetPrim()):
                if "OmniPhysicsDeformablePoseAPI:custom" not in applied_schema_tokens(prim):
                    raise ValueError("Native render skin pose API missing")
                purposes = prim.GetAttribute("deformablePose:custom:omniphysics:purposes")
                bind_points = prim.GetAttribute("deformablePose:custom:omniphysics:points")
                if not purposes or str(purposes.GetTypeName()) != "token[]" or list(purposes.Get()) != ["bindPose"] or purposes.IsCustom() or not bind_points or str(bind_points.GetTypeName()) != "point3f[]" or bind_points.IsCustom() or not np.array_equal(np.asarray(prim.GetAttribute('points').Get()),np.asarray(bind_points.Get())):
                    raise ValueError("Native render skin bind-pose data changed")
        if "bounds_world_m" in record and not np.allclose([points.min(axis=0),points.max(axis=0)],record["bounds_world_m"],rtol=0,atol=tolerance):
            raise ValueError("Cable world bounds changed")
        for prop in record["authored_properties"]:
            attr = stage.GetPrimAtPath(prop["prim_path"]).GetAttribute(prop["attribute"])
            if not attr or str(attr.GetTypeName()) != prop["type"] or attr.IsCustom() or not attr.HasAuthoredValueOpinion():
                raise ValueError("Native cable property type changed: " + prop["attribute"])
            actual, value = attr.Get(), prop["value"]
            if (actual != value if isinstance(value,(bool,str,int)) else not math.isclose(actual,value,rel_tol=2e-6,abs_tol=1e-12)):
                raise ValueError("Native cable property changed: " + prop["attribute"])
        material_prim = stage.GetPrimAtPath(record["material_path"])
        required_material_apis = {MATERIAL[field][0] for field in record["declaration"]["material"]}
        if not material_prim or not required_material_apis <= applied_schema_tokens(material_prim):
            raise ValueError("Native deformable material APIs missing")
        binding = UsdShade.MaterialBindingAPI(root).GetDirectBinding("physics").GetMaterialPath()
        if str(binding) != record["material_path"]:
            raise ValueError("Native deformable material binding changed")
        filtering_input=record["declaration"]["collision"].get("attachment_filtering")
        filtering_scope=filtering_input["value"] if filtering_input is not None else "whole_body"
        if "attachment_filtering" in record and (record["attachment_filtering"]["effective_scope"]!=filtering_scope or record["attachment_filtering"]["input"]!=filtering_input):
            raise ValueError("Native attachment filtering scope/input changed")
        if filtering_scope=="endpoint_elements":
            policy=record.get("attachment_filtering",{})
            expected_length=mesh_spec["filter_length"]["value"] if official else record["declaration"]["geometry"]["length"]["value"]/mesh_spec["axial_segments"]["value"]
            expected_policy="reference_projection_physical_length" if official else "cap_plus_first_axial_span"
            if (policy.get("selection_policy")!=expected_policy
                    or policy.get("endpoint_axial_span_count")!=(None if official else 1)
                    or not math.isclose(policy.get("endpoint_span_length_m",-1),expected_length,rel_tol=1e-12,abs_tol=1e-12)
                    or (official and policy.get("physical_length_input")!=mesh_spec["filter_length"])):
                raise ValueError("Native endpoint filter selection policy/range changed")
        expected_filters = {a["rigid_body_path"] for a in record["attachments"]} if filtering_scope=="whole_body" else set()
        actual_filters = {str(p) for p in UsdPhysics.FilteredPairsAPI(tet.GetPrim()).GetFilteredPairsRel().GetTargets()}
        if actual_filters != expected_filters:
            raise ValueError("Native deformable attached-plug collision filters changed")
        if filtering_scope=="endpoint_elements":
            from .cable_collision_filters import validate_endpoint_filters
            if UsdPhysics.FilteredPairsAPI(root).GetFilteredPairsRel().GetTargets():
                raise ValueError("Native endpoint element filtering cannot coexist with root whole-body pair filtering")
            for attachment in record["attachments"]:
                body=stage.GetPrimAtPath(attachment["rigid_body_path"])
                targets=UsdPhysics.FilteredPairsAPI(body).GetFilteredPairsRel().GetTargets()
                if any(str(t).startswith(record["prim_path"]) or record["prim_path"].startswith(str(t)+"/") for t in targets):
                    raise ValueError("Native endpoint element filtering cannot coexist with reverse whole-body pair filtering")
            validate_endpoint_filters(stage,record,surface,physics_report)
        elif record.get("element_collision_filters") or any(p.GetTypeName()=="OmniPhysicsElementCollisionFilter" and str(p.GetPath()).startswith(record["prim_path"]+"/") for p in stage.Traverse()):
            raise ValueError("Whole-body filtering report cannot also contain endpoint element filters")
        for attachment in record["attachments"]:
            if official and attachment["vertex_indices"]!=generated["ends"][attachment["endpoint"]]:
                raise ValueError("Native attachment does not select the actual generated endpoint cross-section")
            prim = stage.GetPrimAtPath(attachment["prim_path"])
            if not prim or prim.GetTypeName() != "OmniPhysicsVtxXformAttachment":
                raise ValueError("Native endpoint attachment type changed")
            for rel, target in (("omniphysics:src0",record["simulation_mesh_path"]),("omniphysics:src1",attachment["rigid_body_path"])):
                if [str(p) for p in prim.GetRelationship(rel).GetTargets()] != [target] or not stage.GetPrimAtPath(target):
                    raise ValueError("Native endpoint attachment target changed")
            body = stage.GetPrimAtPath(attachment["rigid_body_path"])
            if not body.HasAPI(UsdPhysics.RigidBodyAPI) or not UsdPhysics.RigidBodyAPI(body).GetRigidBodyEnabledAttr().Get():
                raise ValueError("Native endpoint target is not an enabled rigid actor")
            if list(prim.GetAttribute("omniphysics:vtxIndicesSrc0").Get()) != attachment["vertex_indices"] or not np.allclose(prim.GetAttribute("omniphysics:localPositionsSrc1").Get(),attachment["local_positions_m"],rtol=0,atol=tolerance):
                raise ValueError("Native endpoint attachment vertex/local-frame data changed")
    return {"native_deformable_count":len(records), "native_attachment_count":sum(len(r["attachments"]) for r in records), "simulator_validated":False}
