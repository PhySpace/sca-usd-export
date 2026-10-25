# 产品包物理配置

当前USD候选0.1.8rc2配套physics0.1.8rc1；后者的schema1.8新增普通关节限位、主动force Drive、速度/摩擦、articulation、非零初态及最小Mimic传动，见[执行器与单位合同](joint-actuators.zh-CN.md)。本页下文保留旧被动合同；启用新articulation.fixed_base时原接地簇以anchored rigid root+world FixedJoint表达，ground语义保留，不能将下文旧静态推断解释为禁止这一明确映射。

0.1.6rc1 继承原 0.1.1 的 capture → enrich → 回读最终包 → 导出流程。下文是保留的普通刚体合同；新增 schema 1.4 的线缆表示见[刚体关节线缆](rigid-joint-chain.zh-CN.md)。产品包扩展版本 1.0 继续支持；普通机械字段使用 1.1。没有新增字段时，不生成这些 PhysX 属性；刚体静态/动态推断仍采用原生 ground、fixed 分组、真实连接及旧导出策略。

| 包内字段（`parts[node_id]` 下） | 单位 | 真实 USD 映射 |
| --- | --- | --- |
| `rigid_body.mode` | `token`：`static/dynamic/kinematic` | static 不施加 RigidBodyAPI；其余施加 PhysicsRigidBodyAPI，`physics:rigidBodyEnabled=true`，并按模式写 `physics:kinematicEnabled` |
| `rigid_body.gravity_enabled` | `boolean` | PhysxRigidBodyAPI，`physxRigidBody:disableGravity`，数值取反 |
| `rigid_body.linear_damping` | `1/s` | PhysxRigidBodyAPI，`physxRigidBody:linearDamping` |
| `rigid_body.angular_damping` | `1/s` | PhysxRigidBodyAPI，`physxRigidBody:angularDamping` |
| `collision.contact_offset` | `m` | 真实实例 Mesh 上 PhysxCollisionAPI，`physxCollision:contactOffset` |
| `collision.rest_offset` | `m` | 同上，`physxCollision:restOffset` |
| `contact.friction_combine_mode` | `token` | 实例专用物理材料上 PhysxMaterialAPI，`physxMaterial:frictionCombineMode` |
| `contact.restitution_combine_mode` | `token` | 同上，`physxMaterial:restitutionCombineMode` |

新字段显式参数保存为 `{value, unit, source}`；除 `rigid_body.mode` 外，也可保存 `{mode: "engine_default", unit, source}`，此时不允许 `value`。组合规则仅接受 `average/min/multiply/max`。正 contact offset 必须大于零；rest offset 可为负数。正 rest 必须同时给出显式 contact，且 `rest < contact`；两者显式时始终检查这一关系及 float32 舍入后的关系。不会把碰撞偏移当几何尺寸或名义间隙修改。

显式 static 不接受重力/刚体阻尼数值；其 `engine_default` 项是 `not_applicable`，不创建 API 或 USD 属性，便于先生成模板再填适用项。kinematic 接受并导出这些参数，但当前运动由外部位姿控制，重力/阻尼不产生动态积分效果，报告会说明这一点。原生接地、固定于根或因未支持关系冻结的组不能被改成 dynamic/kinematic。固定合并组只有一个 actor，成员逐字段请求必须一致；不同值或显式值与 `engine_default` 冲突会报出字段和实例 ID，不采用最后写入覆盖。

`engine_default` 与省略均没有对应 USD authored value，区别在于包和报告明确保存了用户请求。适用对象的 `engine_default` 会保留真实 PhysX API token，并移除该属性的本地值；若存在继承的 authored 值而无法回到缺省，会报错。重开验证同时检查 API token、类型、值、是否 authored 以及最终物理材料绑定。`physics.optional_properties` 分别记录 `authored/engine_default/omitted/not_applicable`、原参数、USD 字段/单位、目标 schema fallback 和 `backend_verified=false`。

本机 PhysxSchema 107.3.18 的 schema fallback 为：disableGravity=false、linearDamping=0、angularDamping=0.05、两种 combine=average。两个 offset 的 schema fallback 都是 `-inf` 哨兵：contact 由后端按形状尺度选择，rigid rest 由后端选为零；不能把这些记成用户显式数值，也不能宣称 contact 有固定的通用 0.02 m 默认。证据为该版本 `omni.usd.schema.physx` 的 `plugins/PhysxSchema/resources/generatedSchema.usda`。独立 usd-core 环境不加载 PhysxSchema 二进制，使用真实 API token 与精确类型写属性；静态验证能证明值/缺省状态，实际后端采用的默认值需另用 Isaac Sim 回读确认。

刚体线/角阻尼按 PhysX 速度衰减关系采用 `1/s`，数值不转换；该版本 schema 文档将其标为 dimensionless，这是文档口径差异，不能把它和下文关节 Drive 的有量纲阻尼混用。0.1.1 不增加主动驱动、求解器或场景控制入口。

先用主 SimpleCADAPI 技能完成建模和 capture，再用独立 `sca-physics` 技能补充产品包，最后导出。输入的最终 `.scadpkg` 的根定义 `metadata["sca_physics"]` 是物理配置的唯一持久来源；导出无需外部物理 JSON。重新建模或 capture 后必须重新执行补充。

命名空间属于插件扩展，SCA 原生 Material、Constraint、编辑器及历史回放不会自动识别它。长度从原生毫米转换成 USD 米，密度仍读取原生 Material；不会从名称猜材料、密度、摩擦或阻尼。

配置结构由独立 `sca-physics` 包的 `contract.validate_config` 校验。`parts` 以实例 node_id 绑定并核实 definition_id；`joints` 以 joint_id 绑定并核实 joint_type。字段的 `value/unit/source` 全量保存。来源 `explicit/reference/estimated/default` 分别表示明确输入、参数表、说明了依据的估计、版本化演示默认值。

每个零件实例各自拥有 USD Physics 接触材料，绑定不同的静摩擦、动摩擦、恢复系数。`collision.enabled=false` 保留可见几何并关闭碰撞；静态 `approximation_static=none` 保留三角网格碰撞。动态启用碰撞时 `none` 明确报错。装配排除使用标准 `UsdPhysics.FilteredPairsAPI`，目标为实际实例的碰撞形状。固定关系保留原有刚体合并；同一固定刚体内部 `collision_enabled=true` 无法实现，会明确报错。

关节两端 `collision_enabled=false` 同时设置 USD joint 属性，并给两端刚体组的所有实际 leaf collider 显式补充 `FilteredPairsAPI`。真实 Isaac Sim 发现静态端点会被解析为 world，此时只写 joint 属性仍可能发生接触；显式形状过滤是必要的。报告 `physics.collision_exclusions[*].reasons` 区分关节自动产生的 `joint.collision_enabled=false` 和用户额外的 `assembly.collision_exclusions`，重复形状对合并但保留来源。用户额外排除优先于关节允许碰撞，并明确警告这种覆盖。

旋转关节被动阻尼输入为 `N*m*s/rad`，移动关节为 `N*s/m`。导出使用 `UsdPhysics.DriveAPI` 的 force drive，`stiffness=0`、`targetPosition=0`、`targetVelocity=0`，只产生抵抗相对速度的耗散，不安装电机，也不主动驱动。目标位置在零刚度时不起作用。不能用整个刚体角阻尼替代该配置。

在 kg/m/s 的 stage 中，OpenUSD angular drive damping 的单位为 `kg*m^2/(s*degree)`，因此写入系数为输入 SI 系数乘 `pi/180`；linear damping 为 `kg/s`，数值不变。依据 [OpenUSD DriveAPI](https://openusd.org/release/api/class_usd_physics_drive_a_p_i.html)、[PhysX 关节说明](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/latest/dev_guide/rigid_bodies_articulations/joints.html)、[Isaac Sim 关节调节](https://docs.isaacsim.omniverse.nvidia.com/latest/robot_setup/joint_tuning.html)。

包内 `scene.up_axis` 设置 USD `upAxis`，重力方向随之为 -Z 或 -Y。模型顶点、装配变换、锚点和轴向保持原坐标；设置向上方向不旋转几何。默认值为已核实 SCA 场景约定的 +Z。用户明确使用 `--up-axis` 时允许覆盖，并在报告 `physics.input_configuration.overrides` 中保留覆盖前后完整参数。含扩展的包若通过 `--config` 提供物理字段会报错，应先重新补充产品包。配置文件仍可控制曲面网格精度和旧导出器的密度缺失策略等。

旧包没有扩展时使用可追踪 `export-legacy-demo-v1`：静摩擦 0.5、动摩擦 0.4、恢复 0.1、碰撞开、动态 convexHull、静态 none、阻尼 0、关节两端碰撞关、+Z、无额外排除。报告明确警告该兼容配置未嵌入输入资产。报告 `physics.input_configuration.resolved` 保存所有实际值及来源，不能将这些演示值当作测量结果。

导出后的静态验证会重开 USD，逐项核实接触材料绑定及三参数、碰撞开关及近似、过滤目标、原生密度、关节两端刚体、局部及世界锚点和轴向、限位、被动 Drive 系数及零刚度和零目标、upAxis 与重力。报告的仿真状态独立记录；文件及属性校验通过不能代替实际 Isaac Sim 运行验证。

当前USD0.1.8rc2的物理路径通过可选 `physics` extra精确依赖 `sca-physics==0.1.8rc1`：provision前将 `SCA_PHYSICS_SOURCE` 指向准确物理插件仓库或wheel，由SCA独立runtime安装并记录身份；不要安装到建模环境。无物理metadata且不使用prepare-deformable时不需要该依赖，应在全新几何runtime省略该变量。用户升级顺序与错误提示见[兼容矩阵](compatibility-0.1.8rc2.zh-CN.md)；旧候选与实验版0.2来源保持原样。
