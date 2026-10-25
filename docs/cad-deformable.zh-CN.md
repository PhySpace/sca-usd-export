# 既有 CAD occurrence 的体积可变形体导出（0.1.5rc1 候选）

这条路径继续使用建模后的 `sca-physics` 产品包附加接口。SCA 核心不变；
选择的是完整稳定 occurrence `node_id`，不是共享 definition 或显示名称。
对应声明保存在 `metadata.sca_physics.cables`，类型为 `deformable_volume`。

先准备所选实例的真实闭合 CAD 表面：

```powershell
& $PairPython -m sca_usd_export prepare-deformable assembly_raw.scadpkg `
  node/dual_connector_cable_demo/cable cable-surface.json
```

等价薄接口：

```python
from sca_usd_export.deformable_targets import prepare_deformable_surface
surface = prepare_deformable_surface(package, target_node_id)
```

输出点是世界坐标米，面来自 OCCT 对实际 CAD 的三角化。仅在 `1e-10 m`
精度内合并面缝索引，不移动表面、不删除面、不生成四面体。闭合、流形、
朝向和正体积均须通过。官方体网格构建应使用这些点和三角面，不能再使用
旧 `swept_tetrahedra()`。官方输出及其端部索引、参考构形和观测映射应通过
`sca-physics` 附加接口持久化到同一个 `.scadpkg`。

导出时再次提取同一 CAD 表面并比较 `generated.source_geometry_sha256`。
指纹包括真实世界表面和三角化配置。改变材料不会改变该指纹；改变几何、
坐标、单位或三角化会要求重新生成网格及其依赖索引。物理插件的
`mesh_cache_key()` 还验证完整 CAD 来源快照与网格配置。

```powershell
& $PairPython -m sca_usd_export export final.scadpkg assembly.usda --config export.json
& $PairPython -m sca_usd_export validate assembly.usda
```

`export.json` 可以只包含 `{"meters_per_unit":0.01}`，以厘米存储 USD。
默认仍为米。此选项转换所有已知维度属性，包括点、参考形状、显示绑定点、
变换平移、重力、碰撞距离、附件局部坐标、密度、杨氏模量和惯量；
质量、时间、泊松比、摩擦和每秒阻尼不变。产品包、声明 JSON、报告的
物理量仍为 SI。例：`2e6 Pa` 写作 `20000 kg/(cm*s²)`，
`1500 kg/m³` 写作 `.0015 kg/cm³`，重力为 `981 cm/s²`。
没有放大根 Xform 或改变实际物理尺寸。报告保存原 SI 值；独立验证先核对
最终 USD 的物理值，再在内存中恢复 SI 视图执行严格身份、几何和物理检查。

选中 occurrence 保留原 CAD 语义层级和隐藏的源几何，并通过
`sca:physicsBody`、`sca:physicsGeometry`、`sca:renderGeometry` 追溯独立柔体层级。
它在刚体分组、质量积分和刚性 collider 创建前分流。相同 definition 的其他
occurrence 继续按各自声明导出。实际 CAD 表面是唯一可见 skin，原生 TetMesh
是唯一可变形 collider。密度来自柔体材料；该目标不再写入 CAD 刚体质量。

导出器在 portable Python 3.12 环境直接 author 本机 107.3 官方
`OmniPhysicsDeformableBodyAPI`、`OmniPhysicsVolumeDeformableSimAPI`、
`PhysxBaseDeformableBodyAPI`、材料、pose 和 `OmniPhysicsVtxXformAttachment`。
这不是自编求解器；导出环境不能导入 Isaac/Kit helper。
实际 FEM、接触、附件和显示蒙皮由加载这些官方 schema 的 Omni PhysX 求解。

当前明确限制：此薄合同仍为双端线缆形状，须提供参考中心线、端面方向和
两个刚体端部附件。任意实体的自动附件推断不在本候选范围。
目标原有刚体声明须通过 `convert_rigid=True` 明确移除。原生固定、接地、
装配关节或刚性碰撞排除若涉及目标，将报错；不会静默改写为柔体附件。
文件独立回读及静态检查不能代表原生初始化或材料行为通过。

必要的独立测试（无 pytest 依赖）：

```powershell
& $PairPython tests/test_deformable_targets.py
& $PairPython tests/test_stage_units.py
```
