"""Real CAD package -> independent persisted snapshot -> native USD roundtrip."""

from copy import deepcopy
import math
import json

import numpy as np
import pytest
import simplecadapi as scad
from pxr import Usd

from sca_physics.contract import inventory, resolve_config, validate_config
from sca_physics.package_io import enrich_package
from sca_usd_export.exporter import export_package
from sca_usd_export.validation import validate_usd
from sca_usd_export.cable_mesh import swept_tetrahedra


@pytest.fixture(scope="module")
def cable_input():
    @scad.part(id="native-cable-test-plug")
    def plug():
        item = scad.make_part_rpart("native-cable-test-plug", scad.make_box_rsolid(width=10,height=10,depth=10))
        return scad.assign_material_rpart(item, scad.make_material_rmaterial("test-plastic",density=1200,density_unit="kg/m^3"))
    part = plug()
    @scad.assemble(id="native-cable-test-root",definitions=(part,))
    def assembly():
        item = scad.make_assembly_rassembly("native-cable-test-root")
        item = scad.add_component_rassembly(item,part.value,"a",scad.identity_placement_rplacement())
        # Position along +Z to avoid depending on unpublished placement helpers.
        return scad.add_component_rassembly(item,part.value,"b",scad.make_placement_rplacement(origin=(0,0,100)))
    raw = scad.build_product_package(assembly())
    ids = inventory(raw)
    a,b = sorted(ids["parts"])
    source = {"kind":"estimated","detail":"Small regression-only composite cable approximation; not a measured wire"}
    config = {"source":source,"parts":{a:{"rigid_body":{"mode":"kinematic"}},b:{"rigid_body":{"mode":"dynamic"}}},
        "cables":{"test-wire":{"parent_node_id":ids["root_node_id"],"representation":"physx_deformable_volume","source":source,
            "geometry":{"length":.1,"diameter":.005,"centerline":[[0,0,0],[0,0,.1]]},
            "simulation_mesh":{"method":"swept_tetrahedra","axial_segments":20,"radial_segments":8},
            "material":{"density":1200,"youngs_modulus":2e6,"poissons_ratio":.35,"elasticity_damping":0,"dynamic_friction":0},
            "deformable":{"gravity_enabled":False,"self_collision":False,"linear_damping":0,"solver_position_iterations":64,"enable_speculative_ccd":False},
            "collision":{"enabled":False,"contact_offset":.0005,"rest_offset":0},
            "attachments":[{"endpoint":end,"rigid_node_id":node,"local_frame":{"position":[0,0,0],"quaternion_wxyz":[1,0,0,0]},"source":source} for end,node in (("start",a),("end",b))]}}}
    return raw,config


def test_cable_real_package_native_export_and_tamper(tmp_path,cable_input):
    raw,config = cable_input
    final,preservation = enrich_package(raw,config)
    assert preservation["geometry_material_history_blobs_preserved"]
    path = tmp_path/"cable.scadpkg"
    path.write_bytes(scad.encode_product_package(final))
    independent = scad.read_product_package(path)
    saved = independent.root_definition.metadata["sca_physics"]
    validate_config(independent,saved)
    assert saved["schema_version"] == "1.7"
    assert saved["cables"]["test-wire"]["deformable"]["gravity_enabled"]["value"] is False
    assert saved["cables"]["test-wire"]["deformable"]["enable_speculative_ccd"]["value"] is False
    assert saved["cables"]["test-wire"]["material"]["elasticity_damping"]["value"] == 0
    assert saved["cables"]["test-wire"]["material"]["elasticity_damping"]["unit"] == "1/s"
    report = export_package(path,tmp_path/"cable.usda")
    cable = report["physics"]["cables"][0]
    assert report["status"]["physics_static_validated"]
    assert not report["status"]["simulator_validated"]
    assert cable["tetrahedron_count"] == 480
    assert len(cable["attachments"]) == 2
    assert cable["render_mesh_path"].endswith("/RenderMesh")
    assert np.all(np.asarray(report["calculated"]["scene_bounds_world_m"])[1]>=np.asarray(report["calculated"]["cad_bounds_world_m"])[1])
    stage = Usd.Stage.Open(str(tmp_path/"cable.usda"))
    attr = stage.GetPrimAtPath(cable["material_path"]).GetAttribute("omniphysics:youngsModulus")
    attr.Set(3e6)
    changed = tmp_path/"tampered.usda"
    stage.GetRootLayer().Export(str(changed))
    with pytest.raises(ValueError,match="Native cable property changed"):
        validate_usd(changed,expected=report)
    for label,mutation in (
        ("material_api", lambda st: st.GetPrimAtPath(cable["material_path"]).RemoveAppliedSchema("OmniPhysicsDeformableMaterialAPI")),
        ("collision_filter", lambda st: st.GetPrimAtPath(cable["simulation_mesh_path"]).GetRelationship("physics:filteredPairs").ClearTargets(True)),
        ("gpu_scene", lambda st: st.GetPrimAtPath(report["physics"]["scene"]["prim_path"]).GetAttribute("physxScene:enableGPUDynamics").Set(False)),
        ("scene_timestep", lambda st: st.GetPrimAtPath(report["physics"]["scene"]["prim_path"]).GetAttribute("physxScene:timeStepsPerSecond").Set(60)),
        ("parent_link", lambda st: st.GetPrimAtPath(cable["prim_path"]).GetRelationship("sca:parentOccurrence").ClearTargets(True)),
        ("skin_pose", lambda st: st.GetPrimAtPath(cable["render_mesh_path"]).RemoveAppliedSchema("OmniPhysicsDeformablePoseAPI:custom")),
        ("skin_topology", lambda st: st.GetPrimAtPath(cable["render_mesh_path"]).GetAttribute("faceVertexIndices").Set([0,0,0])),
    ):
        stage.GetRootLayer().Reload()
        mutation(stage)
        changed = tmp_path/(label+".usda")
        stage.GetRootLayer().Export(str(changed))
        with pytest.raises(ValueError,match="Native|Cable"):
            validate_usd(changed,expected=report)


@pytest.mark.parametrize("mutation,match",[
    (lambda c: c["cables"]["test-wire"]["material"].update(joint_stiffness=10),"unknown fields"),
    (lambda c: c["cables"]["test-wire"]["material"].update(static_friction=.6),"unknown fields"),
    (lambda c: c["cables"]["test-wire"]["material"].update(youngs_modulus={"value":2e6,"unit":"N*m/rad"}),"unit"),
    (lambda c: c["cables"]["test-wire"]["material"].update(poissons_ratio=.5),"strictly"),
    (lambda c: c["cables"]["test-wire"]["material"].update(poissons_ratio=-.1),"at least zero"),
    (lambda c: c["cables"]["test-wire"]["geometry"].update(length=.2),"arc length"),
    (lambda c: c["cables"]["test-wire"]["attachments"][0].update(rigid_node_id="invented"),"real rigid"),
    (lambda c: c.update(schema_version="1.1"),"does not allow cables"),
    (lambda c: c["cables"]["test-wire"]["deformable"].update(self_collision=0),"boolean"),
    (lambda c: c["cables"]["test-wire"]["collision"].update(attachment_filtering=False),"token"),
    (lambda c: c["cables"]["test-wire"]["collision"].update(attachment_filtering={"value":"endpoint_elements","unit":"boolean"}),"unit"),
])
def test_cable_invalid_inputs_fail_closed(cable_input,mutation,match):
    raw,original = cable_input
    config = deepcopy(original)
    mutation(config)
    with pytest.raises(ValueError,match=match):
        resolve_config(raw,config)


def test_swept_mesh_is_conforming_positive_and_finite():
    points,tets,faces,ends = swept_tetrahedra([[0,0,0],[0,0,.05],[.02,0,.1]],.005,20,8)
    volumes = [np.linalg.det((points[t[1:]]-points[t[0]]).astype(float))/6 for t in tets]
    assert min(volumes)>0
    assert len(faces)==2*8*20+2*8
    assert len(ends["start"])==9
    assert np.isfinite(points).all()


def test_endpoint_element_filters_persist_and_reopen(tmp_path,cable_input):
    from pxr import UsdPhysics
    raw,original=cable_input
    config=deepcopy(original)
    parameter={"value":"endpoint_elements","unit":"token","source":{"kind":"explicit","detail":"Filter only cap and first span in this regression"}}
    config["cables"]["test-wire"]["collision"]["attachment_filtering"]=parameter
    package,_=enrich_package(raw,config)
    archive=tmp_path/"endpoint.scadpkg"
    archive.write_bytes(scad.encode_product_package(package))
    loaded=scad.read_product_package(archive)
    assert loaded.root_definition.metadata["sca_physics"]["cables"]["test-wire"]["collision"]["attachment_filtering"]==parameter
    report=export_package(archive,tmp_path/"endpoint.usda")
    cable=report["physics"]["cables"][0]
    assert report["status"]["physics_static_validated"]
    assert len(cable["element_collision_filters"])==2
    assert all(len(f["surface_triangle_indices"])==24 for f in cable["element_collision_filters"])
    stage=Usd.Stage.Open(str(tmp_path/"endpoint.usda"))
    tet=stage.GetPrimAtPath(cable["simulation_mesh_path"])
    assert not UsdPhysics.FilteredPairsAPI(tet).GetFilteredPairsRel().GetTargets()
    filtered=stage.GetPrimAtPath(cable["element_collision_filters"][0]["prim_path"])
    filtered.GetAttribute("omniphysics:groupElemIndices0").Set([])
    changed=tmp_path/"tampered-endpoint.usda"
    stage.GetRootLayer().Export(str(changed))
    with pytest.raises(ValueError,match="indices/counts"):
        validate_usd(changed,expected=report)



@pytest.mark.parametrize("value", [0.0, 0.01])
def test_elasticity_damping_inverse_seconds_persists_without_rescaling(tmp_path, cable_input, value):
    raw, original = cable_input
    config = deepcopy(original)
    parameter = {"value": value, "unit": "1/s", "source": {
        "kind": "explicit", "detail": "107.3 PhysxDeformableMaterialAPI damping unit regression"}}
    config["cables"]["test-wire"]["material"]["elasticity_damping"] = parameter
    final, _ = enrich_package(raw, config)
    path = tmp_path / "inverse-seconds.scadpkg"
    path.write_bytes(scad.encode_product_package(final))
    independent = scad.read_product_package(path)
    saved = independent.root_definition.metadata["sca_physics"]
    validate_config(independent, saved)
    assert saved["cables"]["test-wire"]["material"]["elasticity_damping"] == parameter
    report = export_package(path, tmp_path / "inverse-seconds.usda")
    cable = report["physics"]["cables"][0]
    authored = next(p for p in cable["authored_properties"] if p["attribute"] == "physxDeformableMaterial:elasticityDamping")
    assert authored["input"] == parameter
    assert authored["value"] == value
    stage = Usd.Stage.Open(str(tmp_path / "inverse-seconds.usda"))
    actual = stage.GetPrimAtPath(cable["material_path"]).GetAttribute("physxDeformableMaterial:elasticityDamping").Get()
    assert actual == pytest.approx(value)


def test_elasticity_damping_dimensionless_unit_rejected_on_input_and_saved_readback(cable_input):
    raw, original = cable_input
    config = deepcopy(original)
    config["cables"]["test-wire"]["material"]["elasticity_damping"] = {
        "value": 0.01, "unit": "1", "source": {
            "kind": "explicit", "detail": "Known old rc1 unit error must fail explicitly"}}
    with pytest.raises(ValueError, match="elasticity_damping.unit: expected '1/s'"):
        resolve_config(raw, config)
    saved = resolve_config(raw, original)
    saved["cables"]["test-wire"]["material"]["elasticity_damping"]["unit"] = "1"
    with pytest.raises(ValueError, match="elasticity_damping.unit: expected '1/s'"):
        validate_config(raw, saved)
