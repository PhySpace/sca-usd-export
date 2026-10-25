# 普通关节物理、执行器与固定基座

当前USD0.1.8rc2消费准确配套 `sca-physics==0.1.8rc1` 保存的schema1.8；旧1.0–1.7包继续按原合同读取，省略新字段不增加主动控制、摩擦或速度限制。具体实测旧包和环境见[兼容矩阵](compatibility-0.1.8rc2.zh-CN.md)，不据合同范围宣称全部历史组合通过。新字段必须经physics公开接口写入产品包，再由新进程导出。导出 `--config` 不补建这些包内声明。普通固定关系继续合并成刚体簇；专用线缆D6路径保持分离。

`joints[stable_joint_id]` 可声明 `motion`（free/limited）、`lower_limit`、`upper_limit`、`max_velocity`、`friction` 和完整 `drive` 块。值、单位、来源 wrapper 由配套 physics 校验与保存，导出报告保留原参数。旋转坐标使用 rad，移动坐标使用 m；速度使用 rad/s、m/s。显式 limited 必须给出两限，free 不携限；省略 motion 时沿用原生 CAD 限位或旧 unbounded 策略。实验限位、执行器增益与上限不能写成真实硬件规格。

`drive` 保存 `mode`（off/position/velocity）、`type`（本轮仅 force）、`target_position`、`target_velocity`、`stiffness`、`damping`、`max_force`。旋转刚度、阻尼、上限分别为 N*m/rad、N*m*s/rad、N*m；移动分别为 N/m、N*s/m、N。position 要求 K>0；velocity 要求 K=0、D>0；off 保留声明但不启用主动增益/目标/上限。max_force 是执行器近似的输出配置上限，不是测量力矩或电机额定扭矩。本轮不模拟电机电气、转子、减速效率或电压电流。

一个轴只有一个 force Drive 通道。已有被动阻尼 Dp 与主动阻尼 Da 组合为 `D=Dp+Da`，有效速度目标为 `Da/(Dp+Da)*v_target`（D=0 时为 0），K 与位置目标保持；因此合力为 `K*(q_target-q)+Da*(v_target-v)-Dp*v`。主动 max_force 限制整个组合通道，包含被动项，不能声称两者独立限幅。active off 或旧包只保留原零刚度、零目标、无显式力上限的被动阻尼。

米制 authoring 时旋转目标、速度、限位乘 180/pi 成 degree；角 K、D 乘 pi/180 成每度系数；力矩上限不乘角度比例。厘米舞台再把角 K、D、力矩和惯量乘 10000，移动位置、速度和力上限乘 100；移动 K、D 不变。声明及报告始终为 SI。只改 `metersPerUnit` 标签或只换目标角度会产生错误物理量。

`friction` 映射本机 107.3 的 `PhysxJointAPI.physxJoint:jointFriction`，单位 1，表示与父子链接传递力/力矩相关的关节摩擦系数。它不是接触材质摩擦、刚体 angular damping、粘性关节阻尼或 Drive 阻尼。`max_velocity` 映射同 API 的 `physxJoint:maxJointVelocity`，角为 degree/s、线为舞台长度/s。两者只对 articulation 有效；未启用 articulation 时明确拒绝导出，避免静默保存而不生效。新 107.3 文档还描述其他 per-axis 摩擦机制，本轮没有将其混入旧系数合同。

`assembly.articulation` 保存 `enabled`、`root_node_id`、`fixed_base`，root 必须是精确稳定实例 ID 或根 assembly ID。导出器解析原生 fixed 组，拒绝不连通、闭环、额外静态端点、kinematic 链接和不支持关节。fixed_base=true 时，原接地根组成为有质量/惯量的 anchored rigid root link，并通过导出器写出的 world FixedJoint 固定；ArticulationRootAPI 位于该 FixedJoint。根初始非原点变换写入 world-side joint frame。原 ground/fixed 语义与 CAD 几何不变。报告区分 anchored root actor、可动 actor、静态 collider、运动 DOF 和全部 USD joints（后者包括新 world FixedJoint）。fixed_base=false 的 floating root 必须为未接地动态树。

各 ordinary joint 的初始位置取已求解两 connector frame 的相对坐标，保留原生 CAD parameters，并通过 `PhysicsJointStateAPI:angular|linear` 写入真实非零初态及零初速。添加 ArticulationRootAPI 后若不写 JointStateAPI，PhysX 会把关节初态置零。初态属性仅用于初始化，运行控制不能每步写 state 或 Transform。静态校验核对 API、关系、初态、frames、限位、合成 Drive、PhysX 参数与单位；真实 link/DOF 拓扑和运动仍必须在本机 Isaac 中独立验证。

articulation 可显式保存 `self_collisions` boolean wrapper；省略时导出 `PhysxArticulationAPI.physxArticulation:enabledSelfCollisions=true`，在报告记录默认策略和来源，不回填产品包。逐对 collider 排除继续生效，不通过全关 self contact 掩盖装配问题。迭代次数与休眠阈值仍为独立运行设置，不能据本案例无条件推广所有资产。

运行时最小入口：

```python
from sca_usd_export import update_drive_targets
# stage 已打开正式导出的 USD；稳定 ID 来自包 inventory / 导出报告。
update_drive_targets(stage, "joint/arm6/J1", target_position=0.2617993877991494)
update_drive_targets(stage, "joint/arm6/J1", target_velocity=0.1)
```

位置与速度参数为 SI，函数读取已导出的 Drive 模式、被动组合权重与舞台长度单位，只修改已存在的 target 属性；缺少 API/转换元数据、off、错误稳定 ID、非法目标或超出位置限位均报错。不会创建 Drive 或修补物理配置。此模块可单独在 Isaac Python 中加载，不要求安装 CAD 依赖；正常配套 runtime 可从包根导入。使用 Tensor 控制时也必须遵守同一权重和单位语义，不能把原始 v_target 当作组合 Drive 的有效 v_target。

最小传动使用顶层 `couplings[id]`：`representation:"mimic"`、精确 `reference_joint_id`/`follower_joint_id`、`gearing`（m/rad wrapper）、`offset`（m wrapper）和声明 `source`。只接受同一 articulation 的 limited Revolute→limited Prismatic，方程 `q_follower+gearing*q_reference+offset=0`；几何初态也必须满足该式。follower 不允许独立主动控制，被动阻尼必须为0，导出不写 follower DriveAPI。用本机已验证 `PhysxMimicJointAPI:rotX`，referenceJointAxis=rotZ（这两个标签在本机单DOF上被忽略），gearing 写 m/degree 数值 `g*pi/180`，厘米舞台再乘100；offset按长度转换。该约束表示理想角到线传动，不模拟齿面接触、齿隙、弹性和损耗，不由此得到通用传动系统。

版本依据是本机 `C:/isaac-sim` PhysX/schema 107.3.18+107.3.1 的 `PhysxSchema/resources/{schema,generatedSchema}.usda`，以及 [107.3 joints](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/rigid_bodies_articulations/joints.html)、[107.3 articulations / JointStateAPI](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/rigid_bodies_articulations/articulations.html)、[107.3 USD Physics schema](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/schemas/usdphysics.html)。API 可构造、静态重开与真实运行行为分别报告，不由文档推定整机通过。
