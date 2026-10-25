"""Stdlib surface tests; no simulator or optional test dependency required."""
import unittest
from types import SimpleNamespace
import numpy as np
from sca_usd_export.deformable_targets import _weld_closed_surface


def _routing_fixture():
    import simplecadapi as scad
    from pxr import Gf, Usd, UsdGeom
    from sca_usd_export.config import ExportConfig
    from sca_usd_export.embedded_physics import legacy_settings
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, '/Model')
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.SetStageUpAxis(stage,'Z')
    stage.CreateClassPrim('/Model/Definitions')
    part = scad.make_part_rpart('cad-routing-test',scad.make_box_rsolid(width=10,height=10,depth=10))
    vertices, faces = scad.exporter.tessellate_solid(part.body.wrapped,linear_deflection=.1,angular_deflection_degrees=20,relative=False)
    mesh = UsdGeom.Mesh.Define(stage,'/Model/Definitions/Shared/Mesh')
    mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in vertices*.001])
    mesh.CreateFaceVertexCountsAttr([3]*len(faces))
    mesh.CreateFaceVertexIndicesAttr(faces.reshape(-1).tolist())
    mesh.CreateSubdivisionSchemeAttr('none')
    UsdGeom.Xform.Define(stage,'/Model/Structure')
    records = [dict(node_id='node/root',parent_node_id=None,definition_id='root',kind='assembly',
        prim_path='/Model/Structure',item=None,world_matrix=np.eye(4))]
    for index, name in enumerate(('selected','spare')):
        path='/Model/Structure/'+name
        UsdGeom.Xform.Define(stage,path)
        matrix=np.eye(4)
        matrix[0,3]=index*.02
        records.append(dict(node_id='node/root/'+name,parent_node_id='node/root',definition_id='shared',
            kind='part',prim_path=path,definition_mesh_path=str(mesh.GetPath()),item=part,world_matrix=matrix,
            volume_m3=1e-6,center_of_mass_local_m=np.array([0.,0.,.005])))
    graph=SimpleNamespace(root_node_id='node/root',ground_edges=[],joints=[],connectors=[])
    config=ExportConfig()
    settings=legacy_settings(records,graph,config)
    settings['cables']={'selected':{'target_node_id':'node/root/selected'}}
    return stage,records,graph,config,settings


class CADSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.points = np.array([[0,0,0], [1,0,0], [0,1,0], [0,0,1]], dtype=float)
        self.faces = np.array([[0,2,1], [0,1,3], [0,3,2], [1,2,3]])

    def test_seams_weld_without_moving_points_or_dropping_faces(self):
        points, faces, volume = _weld_closed_surface(self.points[self.faces].reshape(-1,3), np.arange(12).reshape(-1,3))
        self.assertEqual(len(points), 4)
        self.assertEqual(len(faces), 4)
        self.assertEqual(set(map(tuple, points)), set(map(tuple, self.points)))
        self.assertAlmostEqual(volume, 1/6)

    def test_open_surface_rejected(self):
        with self.assertRaisesRegex(ValueError, 'closed two-manifold'):
            _weld_closed_surface(self.points, self.faces[:-1])

    def test_wrong_winding_rejected(self):
        faces = self.faces.copy()
        faces[0] = faces[0][::-1]
        with self.assertRaisesRegex(ValueError, 'inconsistent surface winding'):
            _weld_closed_surface(self.points, faces)

    def test_duplicate_faces_rejected_without_deletion(self):
        with self.assertRaisesRegex(ValueError, 'duplicate surface faces'):
            _weld_closed_surface(self.points, np.vstack([self.faces, self.faces[0]]))

    def test_selected_shared_definition_occurrence_is_excluded_from_rigid_mass(self):
        from sca_usd_export.physics import apply_physics
        stage,records,graph,config,settings=_routing_fixture()
        report=apply_physics(stage,records,graph,config,settings)
        self.assertEqual(report['collider_count'],1)
        self.assertFalse(any('node/root/selected' in group['members'] for group in report['groups']))
        self.assertTrue(any('node/root/spare' in group['members'] for group in report['groups']))
        self.assertAlmostEqual(report['groups'][0]['mass_kg'],.001)

    def test_native_grounded_target_and_rigid_conflict_fail_explicitly(self):
        from sca_usd_export.physics import apply_physics
        stage,records,graph,config,settings=_routing_fixture()
        graph.ground_edges.append({'parent_node_id':'node/root','child_node_id':'node/root/selected'})
        with self.assertRaisesRegex(ValueError,'grounded'):
            apply_physics(stage,records,graph,config,settings)
        graph.ground_edges.clear()
        settings['parts']['node/root/selected']['rigid_body']={'mode':{'value':'dynamic'}}
        with self.assertRaisesRegex(ValueError,'rigid_body declaration conflicts'):
            apply_physics(stage,records,graph,config,settings)


if __name__ == '__main__':
    unittest.main()
