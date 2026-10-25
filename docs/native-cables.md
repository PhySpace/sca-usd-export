# 原生体变形线缆：候选 schema 1.2

顶层新增 `cables` 对象，键为唯一 cable_id。每条记录包含：

- `parent_node_id`：真实 assembly occurrence；`representation: "physx_deformable_volume"`；`source`：kind/detail 及可选 citation/conditions。
- `geometry`：`length`、`diameter`、`centerline`，均为 SI m。中心线是世界坐标折线，length 必须等于折线弧长。没有原生 SCA cable BREP，也不生成假刚体。
- `simulation_mesh`：`method: "swept_tetrahedra"`，正整数 `axial_segments`、`radial_segments`。截面为带中心顶点的规则多边形；网格离散精度不是柔性材料参数。
- `material`：必需 `density` (kg/m^3)、`youngs_modulus` (Pa)、`poissons_ratio` (1，0 ≤ ν < 0.5)；可选 `dynamic_friction` (1)、`elasticity_damping` (1/s)。这是均质等效有限元材料，不是导体/绝缘复合截面的实测完整模型。
- `deformable` 可选：`gravity_enabled`、`self_collision` 布尔；`linear_damping` (1/s)、`solver_position_iterations` (count, 1..255)。省略不注入默认值。
- `collision` 必需：`enabled` 布尔、`contact_offset` (正 m)、`rest_offset` (m 且小于 contact)。只有一个 native TetMesh collider。
- `attachments`：恰好 start/end 两项。每项含真实 `rigid_node_id`、`local_frame: {position, quaternion_wxyz}` 和 source。position 是该 part 的局部 m，四元数为 wxyz 单位四元数。导出后按原生 fixed group 解析到真实 dynamic/kinematic rigid actor，禁止绑定静态组或显示名。

数值输入可用裸值，或 `{value,unit,source}`。持久化后全部为完整 wrapper，独立回读严格检查。
显式 0/false 保留；未知字段、null、错误 SI 单位、虚构 ID、错误端点或不支持的表示均报错。
材料不从名称自动补值。`static_friction` 在目标原生 deformable 后端未支持，拒绝该字段；
刚性零件的 contact.static_friction 沿用旧支持。端点是原生硬约束，目标后端不支持 attachment
stiffness/damping，因此不暴露这些字段，不把关节刚度称为 Young 模量。

USD 使用 OmniPhysicsDeformableBodyAPI + PhysxBaseDeformableBodyAPI，
UsdGeom.TetMesh + OmniPhysicsVolumeDeformableSimAPI，OmniPhysicsDeformableMaterialAPI
及 PhysxDeformableMaterialAPI。仿真/rest 拓扑相同。两端使用
OmniPhysicsVtxXformAttachment，绑定整个端截面顶点，世界点转换到真实 rigid actor 局部坐标。
local_frame 原点与求解后的 SCA 端点检查一致；四元数保留为端口方向来源，原生附件本身不另写 quaternion，
每顶点局部位置定义截面方向约束。USD 报告逐项保存属性类型/数值、网格摘要与 attachment 目标，并重开核对。

保留的 whole_body 模式将整条线缆与自身两个连接插头组做 FilteredPairs 排除，以避免焊接端重叠接触；
线缆仍可与台面及其他刚体接触。whole_body 是旧策略选择，不是后端限制；新增 endpoint_elements 模式只过滤端部表面三角形，具体选择在每条报告中明确记录。
多边形截面导致实际网格体积小于理想圆体积，报告包含实际 tet volume、density×volume 质量及相对圆体积误差。
弯曲扫掠还引入离散误差；不将理想圆质量谎称为引擎质量。

目标环境：本机 Isaac Sim 5.0 / Omni PhysX 107.3.18，GPU dynamics + GPU broadphase + TGS。
CPU dynamics 明确拒绝 native deformable。运行时临时设置
`/persistent/physics/enableDeformableBeta=true`，启动 `--/app/settings/persistent=false`，不写用户持久配置。
USD 属性重开成功只代表静态描述；后端材料响应、碰撞及附件行为必须由真实 Isaac 运行另证。

原生接口和限制依据：[NVIDIA Omni Physics 107.3 deformable beta](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/deformables_beta/deformable_beta.html)。

渲染由同一原生变形体下的 RenderMesh 表面网格完成，sim/skin 使用 OmniPhysicsDeformablePoseAPI:custom bindPose。物理后端自动驱动 skin；不通过逐帧脚本伪造变形，也不给 skin 添加碰撞、刚体或质量。TetMesh 为 guide，skin 为 default purpose。报告明确旧 bounds_world_m/cad_bounds_world_m 仅含 CAD，scene_bounds_world_m 包含初始线缆表面，供完整资产取景。

含原生线缆时，导出场景策略显式保存 physxScene:timeStepsPerSecond=480（uint，目标 schema fallback=60）。这来自本案例原生验收和 viewer 的 1/480 s 稳定步长假设，未做时步收敛标定；报告记录来源。无 cables 的旧资产不写此项。普通 GUI 使用导出 USD 的已保存场景步频，验证器检查属性类型、数值及 authored 状态。

可选 `deformable.enable_speculative_ccd` 为严格布尔，映射本机 PhysxBaseDeformableBodyAPI 的 `physxDeformableBody:enableSpeculativeCCD`。原生后端根据速度动态调整 contact margin；本案例显式选择 true，用于修复落台瞬态接触穿入。它不是 rigid-body CCD，也不实现自定义求解器。显式 false 原样保存；省略不补值、不写 USD opinion，保留原生 false 默认。选择及依据应保存在参数 source 中。

可选 `collision.attachment_filtering` 是 token 参数 wrapper，仅接受 `whole_body` 或 `endpoint_elements`（unit=token，保留 source）。省略不补入包，沿用既有整线与两个连接刚体组 pair-filter 语义，报告注明兼容来源；显式 whole_body 同义。endpoint_elements 使用原生 OmniPhysicsElementCollisionFilter，仅过滤各自端盖和首个轴向区间的表面三角形，对该端真实刚体组的每个 collider 分别建立过滤器，其余线段仍与插头碰撞。固定生成策略为一个 span，要求 axial_segments≥3，保证中部存在未过滤区域；这不是可变材料/约束参数。报告列出实际 surfaceFaceVertexIndices 行 ID、span 长度和所有 collider 路径；新模式禁止整线 FilteredPairs 残留，验证器按拓扑重新选取并核对 uint[] group counts/indices 与关系。volume–rigid 元素过滤受 107.3 支持，旧 whole-body 选择不是后端能力限制。该功能的实际接触效果仍须单独运行验证。


## rc2 弹性阻尼单位修正

`material.elasticity_damping` 的单位为 `1/s`，对应本机 107.3
`PhysxDeformableMaterialAPI.physxDeformableMaterial:elasticityDamping`。
依据：[107.3 原生 schema 文档](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/deformables_beta/physx_deformable_schema.html#physxdeformablematerialapi)。
rc1 将该字段标作 `1` 是单位合同错误。rc2 对显式 `unit:"1"` 报错，
不会静默换算、接收错误标注或改写原包。需要调用者在确认原数值含义后
另存 `unit:"1/s"` 的新配置，并按原 enrich → 独立回读 → export 流程生成新资产。
未带单位的标量简写仍按该字段既定 SI 单位解释；显式 `0` 保留。
这次修正不会将原数值 `.01` 乘除任何比例；USD 的数值映射不变。
schema 1.0/1.1 与旧流程保持兼容。这是独立合同修正，**不代表已解决拉伸、
传力、反转或动作目标失败**。

## rc3 官方体网格持久化

独立候选新增 `simulation_mesh.method: "physx_conforming_tetrahedra"`，仍为后附加
schema 1.2；未改建模接口。`axial_segments`、`radial_segments` 保留参数 wrapper，
含义为输入闭合表面的离散数量，不推断官方输出的节点编号。

此方法另需 `filter_length: {value, unit:"m", source}` 和 `generated` 对象。过滤长度是
显式固定物理长度，必须正且小于总长一半，不随输出网格的分段隐式变化。
`generated` 包含 `points` (N×3, 世界 m)、`tets` (T×4)、`surface` (F×3)、
`reference_points` (N×3)、`ends:{start:[indices],end:[indices]}`、`provenance`，
可选 `centerline_embedding`。enrich 对省略 reference_points 的显式生成输入补入 points；
独立回读必须具有完整字段。本候选要求无初始预应变，reference_points 等于 points。

`provenance.method` 必须等于此 method，`provenance.api` 必须精确为
`omni.physx.get_physx_cooking_interface().compute_conforming_tetrahedral_mesh`；
其他来源字段保留为有限 JSON，实际调用日志由项目交付保存。声明来源不能替代实际调用证据。
合同不自动修复拓扑：严格检查索引、重复元、float32 后正有向体积、真实 tet 边界表面、
全节点被使用、非流形面、声明管径范围和两端实际完整截面。端截面几何容差为 2 µm。

导出独立读取此 generated 数据，使用相同 points/tets 作为原生 rest shape，按新的 ends
建立双向原生附件。端部碰撞过滤将参考点投影到声明中心线的最近折线段，求弧长，
只选择全部三个顶点都在端部 `filter_length` 内的实际表面三角形；弧长比较容差为
`1e-7 m`。报告记录策略、物理长度输入、实际 face 行 ID、生成来源和完整原始声明。
静态重开再验证 mesh/reference、来源、附件顶点与过滤结果。原始轴向/环索引不复用。

有限适配检查：四个修改模块通过 AST 解析；一个显式标注为合成的三角柱合同样例通过
完整化和独立严格回读，反向体元、缺失边界面、不完整端截面、未知 API 来源均被拒绝。
这只验证合同及适配，不是官方生成调用或真实仿真通过证据。
