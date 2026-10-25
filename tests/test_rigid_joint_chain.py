"""Real CAD -> persisted package -> public export -> independent reopen checks."""
from copy import deepcopy
import json
import math

import numpy as np
import pytest
import simplecadapi as scad
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from sca_physics.contract import inventory, validate_config
from sca_physics.package_io import enrich_package
from sca_physics.rigid_chain import upsert_rigid_chain
from sca_usd_export.config import ExportConfig
from sca_usd_export.exporter import export_package
from sca_usd_export.rigid_joint_chain import capsule_inertia, plan_chain, scene_settings
from sca_usd_export.validation import validate_usd


def raw_fixture():
    @scad.part(id="rigid-chain-test-part")
    def part():
        item = scad.make_part_rpart("rigid-chain-test-part", scad.make_box_rsolid(width=100, height=5, depth=5))
        return scad.assign_material_rpart(item, scad.make_material_rmaterial("chain-test-polymer", density=1200, density_unit="kg/m^3"))
    definition = part()

    @scad.assemble(id="rigid-chain-test-root", definitions=(definition,))
    def assembly():
        item = scad.make_assembly_rassembly("rigid-chain-test-root")
        for name in ("wire", "other_wire", "left", "right"):
            item = scad.add_component_rassembly(item, definition.value, name, scad.identity_placement_rplacement())
        return item
    raw = scad.build_product_package(assembly())
    ids = inventory(raw)
    return raw, {key.rsplit('/', 1)[-1]: key for key in ids['parts']}


def declaration(ids, *, n=8, start="body_fixed", end="body_fixed", shifted=False):
    points = [[0, 0, .0025], [.05, 0, .0025], [.1, 0, .0025]]
    if shifted:
        points = [[x, y+.02, z] for x, y, z in points]
    return {
        "representation": "rigid_joint_chain", "source": {"kind": "estimated", "detail": "Synthetic regression inputs, not a calibrated cable"},
        "geometry": {"coordinate_space": "world", "initial_shape": "planar", "centerline": points,
                     "arc_stations": [0, .05, .1], "free_start": 0, "free_end": .1, "length": .1},
        "discretization": {"free_segments": n, "frame_policy": "shortest_arc_x"},
        "section": {"shape": "circle", "radius": .0025, "endpoint_radius": .00325},
        "mechanics": {"linear_density": .04, "bending_rigidity": .0003, "torsional_rigidity": .00015,
                      "joint_damping": .00003, "damping_mode": "per_joint", "rest_state": "straight"},
        "limits": {"minimum_bend_radius": .04, "maximum_bend_angle": 20, "maximum_twist_angle": 90},
        "body": {"linear_damping": .05, "angular_damping": .05, "solver_position_iterations": 64,
                 "solver_velocity_iterations": 8, "enable_speculative_ccd": True, "apply_to_endpoints": True},
        "collision": {"enabled": True, "contact_offset": .00005, "rest_offset": 0,
                      "self_collision": True, "filter_adjacent": True, "filter_endpoint_links": True},
        "contact": {"static_friction": .4, "dynamic_friction": .3, "restitution": .02},
        "scene": {"physics_hz": 480, "gravity_magnitude": 9.81, "solver_type": "TGS", "enable_gpu_dynamics": False, "broadphase_type": "MBP"},
        "endpoints": {"start": {"mode": start, **({"rigid_node_id": ids['left']} if start == 'body_fixed' else {})},
                      "end": {"mode": end, **({"rigid_node_id": ids['right']} if end == 'body_fixed' else {})}},
    }


def saved_package(*, n=8, start="body_fixed", end="body_fixed", second=False):
    raw, ids = raw_fixture()
    config = upsert_rigid_chain(raw, ids['wire'], declaration(ids, n=n, start=start, end=end), chain_id="cable/with spaces")
    mode_source = {'kind': 'estimated', 'detail': 'Synthetic endpoint mode selected by this regression'}
    config['parts'][ids['left']]['rigid_body'] = {'mode': {'value': 'kinematic', 'unit': 'token', 'source': mode_source}}
    config['parts'][ids['right']]['rigid_body'] = {'mode': {'value': 'dynamic', 'unit': 'token', 'source': mode_source}}
    final, _ = enrich_package(raw, config)
    if second:
        config = upsert_rigid_chain(final, ids['other_wire'], declaration(ids, n=5, start='free', end='world_fixed', shifted=True), chain_id="cable with spaces")
        final, _ = enrich_package(final, config)
    independent = scad.read_product_package(data=scad.encode_product_package(final))
    validate_config(independent, independent.root_definition.metadata['sca_physics'])
    return independent


@pytest.mark.parametrize('n,start,end,mpu', [(4, 'free', 'free', 1.0), (8, 'world_fixed', 'free', .01),
                                           (12, 'body_fixed', 'body_fixed', .01)])
def test_public_package_export_roundtrip(tmp_path, n, start, end, mpu):
    package = saved_package(n=n, start=start, end=end)
    path = tmp_path / 'asset.scadpkg'
    path.write_bytes(scad.encode_product_package(package))
    output = tmp_path / 'asset.usda'
    report = export_package(path, output, config=ExportConfig(meters_per_unit=mpu))
    chain = report['physics']['rigid_chains'][0]
    assert len(chain['links']) == n
    assert len(chain['joints']) == n-1 + (start != 'free') + (end != 'free')
    assert chain['total_mass_kg'] == pytest.approx(.004)
    assert not report['status']['simulator_validated']
    assert not report['physics'].get('cables')
    assert validate_usd(output)['physics_values']['rigid_chains']['links_checked'] == n
    assert json.loads(output.with_suffix('.report.json').read_text(encoding='utf-8'))['physics']['rigid_chains'][0]['natural_state'] == 'straight_local_axes'
    stage = Usd.Stage.Open(str(output))
    assert report['physics']['scene']['rigid_chain_runtime_policy'] == {'external_forces_every_iteration': True}
    assert 'external_forces_every_iteration' not in chain['declaration']['scene']
    scene = stage.GetPrimAtPath(report['physics']['scene']['prim_path'])
    external_forces = scene.GetAttribute('physxScene:enableExternalForcesEveryIteration')
    assert external_forces.HasAuthoredValueOpinion() and not external_forces.IsCustom()
    assert str(external_forces.GetTypeName()) == 'bool' and external_forces.Get() is True
    drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath(chain['joints'][0]['path']), 'rotY')
    assert drive.GetStiffnessAttr().Get() == pytest.approx(.0003/(.1/n)*math.pi/180/mpu**2)


@pytest.mark.parametrize('damage', ['false', 'missing', 'wrong_type', 'custom'])
def test_reopened_runtime_force_policy_rejects_scene_tampering(tmp_path, damage):
    output = tmp_path / ('scene-policy-' + damage + '.usda')
    report = export_package(saved_package(), output)
    stage = Usd.Stage.Open(str(output))
    scene = stage.GetPrimAtPath(report['physics']['scene']['prim_path'])
    name = 'physxScene:enableExternalForcesEveryIteration'
    if damage == 'false':
        scene.GetAttribute(name).Set(False)
    else:
        scene.RemoveProperty(name)
        if damage == 'wrong_type':
            scene.CreateAttribute(name, Sdf.ValueTypeNames.String, custom=False).Set('true')
        elif damage == 'custom':
            scene.CreateAttribute(name, Sdf.ValueTypeNames.Bool, custom=True).Set(True)
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match='enableExternalForcesEveryIteration'):
        validate_usd(output)


def test_legacy_report_without_runtime_policy_keeps_static_compatibility(tmp_path):
    output = tmp_path / 'legacy-scene-policy.usda'
    export_package(saved_package(), output)
    report = json.loads(output.with_suffix('.report.json').read_text(encoding='utf-8'))
    del report['physics']['scene']['rigid_chain_runtime_policy']
    output.with_suffix('.report.json').write_text(json.dumps(report), encoding='utf-8')
    stage = Usd.Stage.Open(str(output))
    scene = stage.GetPrimAtPath(report['physics']['scene']['prim_path'])
    scene.RemoveProperty('physxScene:enableExternalForcesEveryIteration')
    stage.GetRootLayer().Save()
    validation = validate_usd(output)
    assert validation['physics_static_ok'] is True
    assert validation['simulator_validated'] is False


@pytest.mark.parametrize('mpu', [1.0, .01])
def test_relief_series_compliance_and_mass_are_invariant_under_refinement(tmp_path, mpu):
    # Two 5 mm rigid endpoint proxies leave a 95 mm spring-length sum in this
    # 100 mm model. This tests the declared discrete map, not native accuracy.
    compliances = []
    for n in (20, 40, 80):
        raw, ids = raw_fixture()
        request = declaration(ids, n=n, start='world_fixed', end='free')
        request['geometry'].update(free_start=.005, free_end=.095)
        settings = upsert_rigid_chain(raw, ids['wire'], request, chain_id='relief-series')
        final, _ = enrich_package(raw, settings)
        package_path = tmp_path / f'chain-{n}.scadpkg'
        package_path.write_bytes(scad.encode_product_package(final))
        output = tmp_path / f'chain-{n}.usda'
        report = export_package(package_path, output, config=ExportConfig(meters_per_unit=mpu))
        assert validate_usd(output)['physics_static_ok'] is True
        stage = Usd.Stage.Open(str(output))
        chain_root = stage.GetPrimAtPath(report['physics']['rigid_chains'][0]['prim_path'])
        links = [prim for prim in Usd.PrimRange(chain_root) if prim.IsA(UsdGeom.Capsule)]
        joints = [prim for prim in Usd.PrimRange(chain_root)
                  if prim.IsA(UsdPhysics.Joint) and not prim.IsA(UsdPhysics.FixedJoint)]
        assert len(links) == n + 2
        assert len(joints) == n + 1
        mass_unit = UsdPhysics.GetStageKilogramsPerUnit(stage)
        length_unit = UsdGeom.GetStageMetersPerUnit(stage)
        actual_mass = sum(UsdPhysics.MassAPI(prim).GetMassAttr().Get() * mass_unit for prim in links)
        assert actual_mass == pytest.approx(.04 * .1, rel=2e-6)
        actual_stiffness = [UsdPhysics.DriveAPI.Get(prim, 'rotX').GetStiffnessAttr().Get()
                            * mass_unit * length_unit**2 * 180 / math.pi for prim in joints]
        compliance = sum(1 / stiffness for stiffness in actual_stiffness)
        assert compliance == pytest.approx((.1 - .005) / .00015, rel=2e-6)
        compliances.append(compliance)
    assert compliances == pytest.approx([compliances[0]] * 3, rel=2e-6)


def test_multiple_named_chains_and_shared_definition(tmp_path):
    package = saved_package(second=True)
    report = export_package(package, tmp_path / 'multi.usda')
    chains = report['physics']['rigid_chains']
    assert len(chains) == 2
    assert len({chain['prim_path'] for chain in chains}) == 2
    assert sorted(len(chain['links']) for chain in chains) == [5, 8]
    assert report['validation']['physics_values']['rigid_chains']['chain_count'] == 2


@pytest.mark.parametrize('damage', ['stiffness', 'mass', 'anchor', 'filter', 'radius', 'ccd'])
def test_independent_reopen_rejects_damage(tmp_path, damage):
    output = tmp_path / ('damage-' + damage + '.usda')
    report = export_package(saved_package(), output, config=ExportConfig(meters_per_unit=.01))
    chain = report['physics']['rigid_chains'][0]
    stage = Usd.Stage.Open(str(output))
    link = stage.GetPrimAtPath(chain['links'][0]['path'])
    joint = stage.GetPrimAtPath(chain['joints'][0]['path'])
    if damage == 'stiffness':
        UsdPhysics.DriveAPI.Get(joint, 'rotY').GetStiffnessAttr().Set(0)
    elif damage == 'mass':
        UsdPhysics.MassAPI(link).GetMassAttr().Set(.5)
    elif damage == 'anchor':
        joint.GetAttribute('physics:localPos0').Set((1, 2, 3))
    elif damage == 'filter':
        link.GetRelationship('physics:filteredPairs').SetTargets([])
    elif damage == 'radius':
        link.GetAttribute('radius').Set(.99)
    else:
        link.GetAttribute('physxRigidBody:enableSpeculativeCCD').Set(False)
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError):
        validate_usd(output)


def test_mass_and_inertia_use_material_arc_not_chord():
    shape = {'geometry': {'centerline': {'value': [[0, 0, 0], [.05, .05, 0], [.1, 0, 0]]},
                          'arc_stations': {'value': [0, .1, .2]}, 'free_start': {'value': 0},
                          'free_end': {'value': .2}, 'length': {'value': .2}},
             'discretization': {'free_segments': {'value': 2}}, 'section': {'radius': {'value': .005}, 'endpoint_radius': {'value': .006}},
             'mechanics': {'linear_density': {'value': .04}}}
    plan = plan_chain(shape)
    assert sum(plan['masses_kg']) == pytest.approx(.008)
    assert sum(plan['arc_lengths_m']) > sum(plan['chord_lengths_m'])
    assert np.allclose(plan['inertia_kg_m2'][0], capsule_inertia(.004, .005, math.sqrt(.005)))


def test_scene_conflicts_are_rejected():
    one = {'scene': {'physics_hz': {'value': 480}, 'solver_type': 'TGS'}}
    two = deepcopy(one)
    two['scene']['physics_hz']['value'] = 240
    with pytest.raises(ValueError, match='conflict'):
        scene_settings({'one': one, 'two': two})


def test_material_neighborhood_filter_is_local_in_arc_coordinate(tmp_path):
    raw, ids = raw_fixture()
    request = declaration(ids, n=80, start='world_fixed', end='free')
    request['collision']['filter_material_neighbors'] = True
    config = upsert_rigid_chain(raw, ids['wire'], request, chain_id='refined')
    final, _ = enrich_package(raw, config)
    report = export_package(final, tmp_path / 'refined.usda')
    chain = report['physics']['rigid_chains'][0]
    pairs = {tuple(pair) for pair in chain['filtered_pairs']}
    links = chain['links']
    assert (links[0]['path'], links[2]['path']) in pairs
    assert (links[0]['path'], links[8]['path']) not in pairs
    assert chain['material_neighbor_filter']['enabled'] is True
    assert validate_usd(tmp_path / 'refined.usda')['ok']


@pytest.mark.parametrize('offset', [.021, .000332145])
def test_planar_curve_with_non_axis_aligned_tangents(tmp_path, offset):
    raw, ids = raw_fixture()
    request = declaration(ids, n=17, start='world_fixed', end='free')
    request['geometry']['centerline'] = [[0, 0, .0025], [.043, offset, .0025], [.1, .005, .0025]]
    points = request['geometry']['centerline']
    first, second = math.dist(points[0], points[1]), math.dist(points[1], points[2])
    request['geometry'].update(arc_stations=[0, first, first+second], length=first+second, free_end=first+second)
    config = upsert_rigid_chain(raw, ids['wire'], request, chain_id='curved')
    final, _ = enrich_package(raw, config)
    assert export_package(final, tmp_path / 'curve.usda')['validation']['ok']


def test_y_up_and_static_body_endpoint_preserve_world_centerline(tmp_path):
    raw, ids = raw_fixture()
    config = upsert_rigid_chain(raw, ids['wire'], declaration(ids, start='body_fixed', end='free'), chain_id='y-up')
    config['scene']['up_axis']['value'] = '+Y'
    config['parts'][ids['left']]['rigid_body'] = {'mode': {'value': 'static', 'unit': 'token', 'source': {'kind': 'estimated', 'detail': 'Regression static endpoint'}}}
    final, _ = enrich_package(raw, config)
    report = export_package(final, tmp_path / 'y-up.usda')
    chain = report['physics']['rigid_chains'][0]
    assert report['configuration']['up_axis'] == 'Y'
    assert report['physics']['scene']['gravity_direction'] == [0, -1, 0]
    endpoint = chain['endpoints'][0]
    assert endpoint['mode'] == 'body_fixed' and endpoint['rigid_body_mode'] == 'static'
    assert endpoint['rigid_body_path'] is None
    assert len(endpoint['collider_paths']) == 1
    assert chain['points_world_m'][0] == [0, 0, .0025]
    assert report['validation']['ok']


@pytest.mark.parametrize('n', [512, 513])
def test_original_segment_boundary_and_larger_budgeted_package_export(tmp_path, n):
    raw, ids = raw_fixture()
    request = declaration(ids, n=n, start='world_fixed', end='free')
    if n > 512:
        request['resource_budget'] = {'max_bodies': 1024, 'max_joints': 1024, 'max_colliders': 1024,
                                      'max_filtered_pairs': 4096, 'max_contact_pairs': 1_000_000,
                                      'max_solver_work_per_second': 80_000_000}
    settings = upsert_rigid_chain(raw, ids['wire'], request, chain_id='boundary')
    final, _ = enrich_package(raw, settings)
    package_path = tmp_path / f'boundary-{n}.scadpkg'
    package_path.write_bytes(scad.encode_product_package(final))
    output = tmp_path / f'boundary-{n}.usda'
    report = export_package(package_path, output)
    chain = report['physics']['rigid_chains'][0]
    assert len(chain['links']) == n and len(chain['joints']) == n
    assert chain['declaration']['discretization']['free_segments']['value'] == n
    assert validate_usd(output)['physics_values']['rigid_chains']['links_checked'] == n
    assert chain['total_mass_kg'] == pytest.approx(.004)
    if n > 512:
        resources = chain['resource_usage']
        assert resources['passed'] is True
        assert resources['estimated_usage']['filtered_pairs'] == n - 1
        assert resources['estimated_usage']['bodies'] == n
        assert report['physics']['scene']['rigid_chain_resources'] == resources
        assert not chain['simulation_verified']
        report_path = output.with_suffix('.report.json')
        persisted = json.loads(report_path.read_text(encoding='utf-8'))
        persisted['physics']['scene']['rigid_chain_resources']['estimated_usage']['bodies'] -= 1
        report_path.write_text(json.dumps(persisted), encoding='utf-8')
        with pytest.raises(ValueError, match='resource usage changed'):
            validate_usd(output)


def test_saved_inference_is_copied_without_reselection_and_reopen_rejects_report_damage(tmp_path):
    from sca_physics.cable_inference import configure_cable

    @scad.part(id='resolution-round-part')
    def round_part():
        solid = scad.make_cylinder_rsolid(radius=1.3, height=300, bottom_face_center=(0, 0, 50), axis=(1, 0, 0))
        part = scad.make_part_rpart('resolution-round-part', solid)
        material = scad.make_material_rmaterial('resolution-cad-polymer', density=1200, density_unit='kg/m^3')
        return scad.assign_material_rpart(part, material)
    definition = round_part()

    @scad.assemble(id='resolution-round-root', definitions=(definition,))
    def round_assembly():
        root = scad.make_assembly_rassembly('resolution-round-root')
        return scad.add_component_rassembly(root, definition.value, 'wire', scad.identity_placement_rplacement())
    raw = scad.build_product_package(round_assembly())
    target = next(iter(inventory(raw)['parts']))
    settings = configure_cable(raw, target, {'description': '做一根普通软电线。', 'category': 'stranded_wire'}, chain_id='resolved-wire')
    final, _ = enrich_package(raw, settings)
    package_path = tmp_path / 'resolved.scadpkg'
    package_path.write_bytes(scad.encode_product_package(final))
    saved = scad.read_product_package(data=package_path).root_definition.metadata['sca_physics']['cables']['resolved-wire']
    output = tmp_path / 'resolved.usda'
    # At 0.3 m, CAD Mesh float32 rounding is 1.19e-8 m. The validated saved
    # export recommendation is consumed before tessellation; physics is unchanged.
    report = export_package(package_path, output)
    chain = report['physics']['rigid_chains'][0]
    assert chain['declaration'] == saved
    assert chain['resolved_configuration'] == saved['resolution']
    assert len(chain['links']) == saved['discretization']['free_segments']['value']
    assert chain['resource_usage']['passed'] is True
    assert report['geometry_tolerance_policy']['origin'] == 'saved_cable_resolution'
    assert report['configuration']['geometry_absolute_tolerance_m'] == saved['resolution']['solver']['export_geometry_absolute_tolerance_m']
    assert validate_usd(output)['physics_static_ok'] is True
    from sca_usd_export import export_package as public_export
    facade = public_export(package_path, tmp_path / 'facade-auto.usda')
    assert facade['geometry_tolerance_policy']['origin'] == 'saved_cable_resolution'
    facade_cm = public_export(package_path, tmp_path / 'facade-cm.usda',
                              config=ExportConfig(meters_per_unit=.01), explicit_config_fields={'meters_per_unit'})
    assert facade_cm['geometry_tolerance_policy']['origin'] == 'saved_cable_resolution'
    # A direct API config object deliberately retains precedence, even when its
    # tolerance equals the old default. It must not be silently loosened.
    with pytest.raises(ValueError, match='float32 error'):
        export_package(package_path, tmp_path / 'explicit-strict.usda', config=ExportConfig())
    assert not (tmp_path / 'explicit-strict.usda').exists()
    from sca_usd_export.cli import main as cli_main
    assert cli_main(['export', str(package_path), str(tmp_path / 'cli-auto.usda')]) == 0
    assert validate_usd(tmp_path / 'cli-auto.usda')['physics_static_ok'] is True
    strict = tmp_path / 'strict.json'
    strict.write_text(json.dumps({'geometry_absolute_tolerance_m': 1e-8}), encoding='utf-8')
    assert cli_main(['export', str(package_path), str(tmp_path / 'cli-strict.usda'), '--config', str(strict)]) == 2
    assert not (tmp_path / 'cli-strict.usda').exists()
    # An unrelated explicit config field does not suppress the saved tolerance.
    other = tmp_path / 'centimetres.json'
    other.write_text(json.dumps({'meters_per_unit': .01}), encoding='utf-8')
    assert cli_main(['export', str(package_path), str(tmp_path / 'cli-cm.usda'), '--config', str(other)]) == 0
    assert validate_usd(tmp_path / 'cli-cm.usda')['physics_static_ok'] is True
    report_path = output.with_suffix('.report.json')
    persisted = json.loads(report_path.read_text(encoding='utf-8'))
    persisted['physics']['rigid_chains'][0]['resolved_configuration']['selection']['free_segments'] += 1
    report_path.write_text(json.dumps(persisted), encoding='utf-8')
    with pytest.raises(ValueError, match='resolved configuration changed'):
        validate_usd(output)


def test_legacy_explicit_chain_keeps_original_geometry_tolerance(tmp_path):
    report = export_package(saved_package(), tmp_path / 'legacy-tolerance.usda')
    assert report['configuration']['geometry_absolute_tolerance_m'] == 1e-8
    assert report['geometry_tolerance_policy']['origin'] == 'centralized_export_default'
    assert report['geometry_tolerance_policy']['saved_recommendations_m'] == {}
