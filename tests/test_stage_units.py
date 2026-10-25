"""Physical dimensions survive opt-in centimetre storage; SI remains default."""
import unittest
import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom
from sca_usd_export.config import ExportConfig
from sca_usd_export.stage_units import convert_stage_units, stage_in_si


class StageUnitsTests(unittest.TestCase):
    def make_stage(self):
        stage = Usd.Stage.CreateInMemory()
        root = UsdGeom.Xform.Define(stage, '/Model')
        stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        transform = np.eye(4)
        transform[3, :3] = [.1234, .04, .005]
        root.AddTransformOp().Set(Gf.Matrix4d(transform.tolist()))
        mesh = UsdGeom.Mesh.Define(stage, '/Model/Mesh')
        mesh.CreatePointsAttr([Gf.Vec3f(.1234, .035, .005)])
        prim = root.GetPrim()
        for name, value in [('omniphysics:youngsModulus', 2e6), ('omniphysics:density',1500),
                ('physics:gravityMagnitude',9.81), ('physxDeformableBody:sleepThreshold',.001),
                ('physxDeformableBody:linearDamping',.2), ('physics:mass',.1)]:
            prim.CreateAttribute(name,Sdf.ValueTypeNames.Float).Set(value)
        prim.CreateAttribute('physics:diagonalInertia',Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(.001,.002,.003))
        prim.CreateAttribute('sca:physicsDeclaration',Sdf.ValueTypeNames.String).Set('{"density":1500}')
        return stage

    def test_centimetre_dimensional_conversion_and_si_roundtrip(self):
        stage = self.make_stage()
        audit = convert_stage_units(stage, .01)
        root = stage.GetPrimAtPath('/Model')
        self.assertAlmostEqual(root.GetAttribute('omniphysics:youngsModulus').Get(),20000)
        self.assertAlmostEqual(root.GetAttribute('omniphysics:density').Get(),.0015)
        self.assertAlmostEqual(root.GetAttribute('physics:gravityMagnitude').Get(),981,places=3)
        self.assertAlmostEqual(root.GetAttribute('physxDeformableBody:sleepThreshold').Get(),.1)
        self.assertAlmostEqual(root.GetAttribute('physxDeformableBody:linearDamping').Get(),.2)
        self.assertEqual(root.GetAttribute('sca:physicsDeclaration').Get(),'{"density":1500}')
        si = stage_in_si(stage,unit_report=audit)
        source = self.make_stage()
        np.testing.assert_array_equal(si.GetPrimAtPath('/Model/Mesh').GetAttribute('points').Get(),
                                     source.GetPrimAtPath('/Model/Mesh').GetAttribute('points').Get())
        self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage),.01)

    def test_changed_physical_geometry_cannot_be_hidden_by_si_clone(self):
        stage = self.make_stage()
        audit = convert_stage_units(stage,.01)
        stage.GetPrimAtPath('/Model/Mesh').GetAttribute('points').Set([Gf.Vec3f(13.34,3.5,.5)])
        with self.assertRaisesRegex(ValueError,'changed physical value'):
            stage_in_si(stage,unit_report=audit)

    def test_legacy_default_no_conversion(self):
        self.assertEqual(ExportConfig().meters_per_unit,1)
        stage=self.make_stage()
        self.assertEqual(convert_stage_units(stage,1)['changed_authored_properties'],[])


if __name__ == '__main__':
    unittest.main()
