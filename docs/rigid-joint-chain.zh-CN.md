# 从最终产品包导出刚体关节线缆

当前候选版本 0.1.8rc2，严格配套 sca-physics==0.1.8rc1，SCA2.2.5/Windows x64/Python3.12。接受物理Schema1.0–1.8（准确实测范围见[兼容矩阵](compatibility-0.1.8rc2.zh-CN.md)）；1.6保存描述解析、最终分段与资源预算，导出不重选N，1.8新增普通关节声明。旧1.4/1.5分段2..512不变，1.6允许明确预算下的更多段。1.5扩展刚体链physics_hz至16384，原1.4仍限4096。物理插件是唯一配置合同来源；本插件不重复维护其 JSON Schema，也不修改原生 CAD。

Schema 1.7 保留严格来源、可重算派生值及任务决策；导出只消费已保存结果。部署与输入哈希保护见安装 payload 的 docs/agent-decisions.zh-CN.md。
使用物理插件 set-rigid-chain（API upsert_rigid_chain）把选定 CAD occurrence 的中心线和完整近似参数保存进新的产品包，独立 validate 后，正常调用：

用户只提供物体描述时，可改用配套physics的`configure-cable`；几何读取、类别来源、自动/目标段长/手工模式、冻结快照及有限运行检查见[自动线缆流程](automatic-cables.zh-CN.md)。下面的export对两类最终包均适用。

```powershell
# PairPython 必须已安装本维护配套。
& $PairPython -I -B -m sca_physics validate final.scadpkg
& $PairPython -I -B -m sca_usd_export export final.scadpkg new.usda --config export.json
& $PairPython -I -B -m sca_usd_export validate new.usda
```

export.json 可为 `{"meters_per_unit":0.01}`。路径由调用者选择，输出必须新建。准备 USD 独立环境前，显式设置 SCA_PHYSICS_SOURCE 为配套物理仓根或 wheel，不能假定候选已发布于包索引。

配置位于 metadata.sca_physics.cables[chain_id]，representation 和 physical_type 均为 rigid_joint_chain。真实 target_node_id、直接 parent、BREP/单位/occurrence 变换指纹由物理插件保存和核对。显示名或 definition_id 不能替代 occurrence。重复目标、陈旧来源、未知字段、缺参、错误单位、与原生固定/接地/关节冲突均报错。替换已有 FEM 必须经过物理插件显式转换，不在 USD 阶段停用旧树。

第一版支持 world SI m 的平面折线、材料弧长、自由区等分和可选两个非零端部 relief；自由段数不固定为50。使用 shortest_arc_x 局部轴、圆截面胶囊、线密度乘材料弧长质量及实心胶囊惯量。胶囊 height 为弦长，半球超出材料站点；重叠胶囊不额外加质量。CAD 源隐藏，胶囊同时显示和碰撞；没有连续 CAD skin 绑定。

mechanics.rest_state=straight 表示各段自然局部轴一致，初始曲线有弹性预弯；不是无应力曲线。D6 平移锁定，角刚度分别为 EI/ell 和 GJ/ell，ell 是相邻材料区间弧长平均值；逐关节阻尼为固定 N*m*s/rad 输入，转成 USD 每度系数。没有自动计算 Young 模量、分布阻尼或任意曲线自然态；不支持的输入拒绝。

端部为 free、world_fixed 或 body_fixed。frame 从端点切线和真实 rigid actor 初始变换派生；world 端没有 body 关系，free 端没有 joint。body_fixed 通过 occurrence 解析固定组实际 actor 和全部 leaf collider，静态组按世界固定处理并保留原 occurrence 来源。body.apply_to_endpoints 显式决定是否把迭代数与预测式 CCD 应用到绑定 actor；不改其质量、阻尼和运动学模式。多链对同 actor 的不一致请求报错。

自接触、相邻排除、端部一段对其绑定组 leaf collider 的过滤都来自保存配置；不永久关闭全部环境接触。多链路径按完整 chain_id 摘要分离，不硬编码 A/B、52 段或场景路径。CPU/TGS/MBP/步频/重力配置须一致；与 GPU 原生 FEM 共场景的冲突拒绝，旧 FEM 单独导出行为不变。

`collision.filter_material_neighbors` 是显式布尔选项，省略/false 保留原相邻过滤语义。true 额外排除材料弧长区间间隙不大于两段半径之和的局部近邻胶囊，含 1e-12 m 数值容差，避免细分时半球帽跨越多段而初始重叠。这是按材料坐标的局部代理过滤；弧长相距远、空间折回接触的线段仍碰撞。报告记录判据；V6 保持 false，不改变原过滤对。

新链位于资产 defaultPrim 内 Physics/RigidChains 子树。report.physics.rigid_chains 保存各段站点、world初始点、质量/惯量、joint body/局部 frame、端部 actor、碰撞对和来源。CAD 语义、BREP、材料和历史保留。导出不读取旧实验目录、V6 build.py 或旧 USD。

重开校验包含 Capsule 尺寸/姿态、质量/惯量、按 EI/GJ 与弧长重算的驱动、平移锁、端部/world锚点、CCD/迭代/接触、实际过滤和对象计数。米/厘米转换包含 radius/height、惯量、锚点、D6 rotX/Y/Z stiffness/damping/torque；报告和声明保持 SI。

USD `MassAPI.centerOfMass` 是 point3f。校验比较 CAD 分析质心正确舍入为 float32 后的值，分析值仍完整保留于报告；这不改变 CAD 几何容差或动态验收门槛。quatf 切线检查独立重算 author 值，再准确复现 USD orient op 的 quatf → Gf.Rotation → 矩阵序列；近零角由实部分量舍入造成的微小方向变化原样保留，不靠放宽方向/几何容差通过。代理包络报告按真实 USD 矩阵计算，理想材料端点另行完整保留。Windows 历史资源读写使用原生长路径形式，manifest 中仍仅保存可移植的相对路径，不要求修改机器注册表。

tests/test_rigid_joint_chain.py 用真实 SCA 产品包覆盖不同段数、端部、多条共享定义 occurrence、单位和篡改拒绝。静态成功不等于原生仿真成功。V6 轨迹、Isaac 步进、接触观测和5秒测试继续留项目；实物参数标定另需测量证据。

## TGS外力积分和定量对照（2026-10-22）

新导出的链场景明确写入 `physxScene:enableExternalForcesEveryIteration=true`，并在 `report.physics.scene.rigid_chain_runtime_policy` 记录。重新打开时会核对该Bool属性的类型、值和authored状态。旧报告不包含此新增派生记录时仍按旧格式验收静态USD，不改写旧资产；这不表示旧力学失败自动通过。该策略不新增用户声明字段；另由physics的Schema1.5承载高步频，原Schema1.4文件不变。步频和迭代数仍由保存配置明确给出。

该开关使外载在TGS各子步施加，原对照中曾减小返回角速度与位姿差分的不一致；它不保证全部速度一致，也不能单独消除耦合弹簧/固定约束的有限子步柔度偏差。本机107.3/PhysX5.6.1对照已证实：动态首段加世界FixedJoint的两段纯扭转存在随 `h=(1/physics_hz)/solver_position_iterations` 减小的误差；用运动学首段隔离单个弹簧后，当前每度到每弧度的刚度映射与解析值一致。不能为抵消此求解误差改变EI、GJ、质量、惯量或每关节阻尼，也不能承诺一组步频适用于所有N和线缆。

该策略作用于rc2所有新导出的刚体链，包括低Hz/Schema1.4和V6；不是只影响高Hz输入。原V6回归的几何、关节、材料、过滤和载荷保持，5秒结构门槛通过，但原生位姿/速度轨迹不同。必须将这一求解行为变化与旧USD/report兼容读取区分，不能宣称V6默认动态行为完全不变。

力学验收应保留同一材料、边界、载荷和原门槛，先在固定N下做必要的步长/迭代对照，检查原生速度、位姿差分、关节平衡，再用同一足够精细的设置比较至少三档N。对于小转角直链纯端扭矩，另从重新打开的USD逐关节读回驱动系数并换回SI，用 `phi=T*sum(1/k_i)` 检查离散求解；与连续杆 `T*L/GJ` 的差另计边界离散误差。N个材料区间只有N−1个内部弹簧，实际柔度长度为 `L-(ds_first+ds_last)/2`。不能把完整自由长度、弦长、材料弧长或刚体数量混作同一个L。

扭转角须由材料轴的相对旋转测量并按需展开；不能使用世界Euler角。原生PxConstraint反力查询不默认包含drive贡献，代数 `k*angle+c*omega` 也不是原生驱动力矩回读。USD和本机原生接口的角单位不同：角驱动USD系数按每度，原生角度/角速度使用rad和rad/s；现有SI到USD的pi/180换算保留。当前仍不提供空间曲线初态、曲线自然态或连续分布阻尼。
