# 从用户描述到已解析线缆的独立 USD 导出

Agent 根据用户用途、已有 CAD、端部条件和物性证据选择配置路径；配套 physics
负责校验并保存声明，USD 插件只消费最终产品包。`configure-cable` 的本地规则
只接受调用者明确选择的现有类别，不依据描述猜类别，不代替 Agent 对对象和证据适用性的判断。插件运行
不调用在线模型；USD 不重新选择材料或段数，也不依赖原中心线文件、用户描述
文件或前一进程的内存。

## 按对象与证据选择路径

| 情况 | 当前可用入口与 Agent 职责 |
|---|---|
| 匹配现有预设的对象 | 核对普通多股软电线或 PVC 跳绳与预设构造/尺寸假设是否相符，用 `configure-cable --config request.json`，在请求中显式写 `category`。预设是起点，不是实物识别或标定。 |
| 自定义且适合当前链模型的细长体 | 用 `set-rigid-chain --config chain.json` 提交完整声明和显式 `discretization.free_segments`。Agent 根据几何、用途、证据及计算成本选择输入；缺少标定的物性可作有依据且标为 `estimated` 的工程初值。该入口没有通用自动选段能力。 |
| 当前模型不适用的对象 | 说明几何、自然态、材料或运动表示的缺口与影响，不为通过校验强归为电线或 FEM。其他既有表示只有符合各自输入/后端条件时才采用；超出实现范围的能力须另行实现。 |

当前预设类别只有 `stranded_wire` 与 `pvc_jump_rope`；省略类别或类别不受支持会
明确拒绝。模型适用的其他对象走完整自定义声明，不强行归类。非圆截面、空间初态、曲线自然态
或连续分布阻尼等不因换入口而获得支持。手工链字段见配套 physics payload 的
`docs/rigid-joint-chains.md`；本仓 [刚体链说明](rigid-joint-chain.zh-CN.md)
说明导出表示和限制。常规刚体补包与既有 FEM 路线仍按其独立合同使用。

参数来源按实际依据记录：用户明确值、匹配资料/标定、类别预设和工程估计不能
混写。从0.1.7rc2开始采用、当前physics0.1.8rc1继续执行的规则按调用者有效输入、evidence、明确选择的预设、默认依次补缺，
不按来源标签压过已经提交的值；非法值不退回预设。来源按逐值、本次组/声明、
本次根来源解析，仍缺来源的显式值报完整路径并拒绝。程序补全值自行标记，不能
继承根部用户标签。供应商尺寸/线质量资料不能
替 EI/GJ、阻尼或摩擦估计背书。预设“更硬/更软”的倍率、直径缩放、初始接触与
求解设置仍是既有工程规则；需要改变物性时，通过 physics 已有输入生成新包。

## 正式流程

先由 SCA 建模并 capture 原始 `.scadpkg`，或读取已有原始包。Agent 选择精确 CAD
occurrence，核对尺寸和真实端部实体。能用合理的可逆假设推进时说明假设；重要
歧义会改变对象、边界或用途时，再作有针对性的确认。已有 CAD 不为迁就预设而
改尺寸。下面仅示例匹配普通软电线预设的请求：

```json
{
  "source": {"kind": "estimated", "detail": "Agent选择的初始用途、端部和计算配置；并非用户逐项指定。"},
  "description": "做一根普通软电线。",
  "category": "stranded_wire",
  "quality": "balanced",
  "discretization": {"mode": "auto"},
  "endpoints": {"start": {"mode": "world_fixed"}, "end": {"mode": "free"}}
}
```

`description` 不等于实物标定。预设匹配时可由已有规则提供初始分段和参数，不必
要求用户提供所有专业数值；自定义手工链仍需要完整输入，不能把未知值假称程序
已自动推断。类别预设和工程估计在最终资产中保留其适用条件和来源。
跳绳手柄应绑定真实 `rigid_node_id`，其 static/dynamic/kinematic 模式由相同
产品包的基础实例配置保存；不能从名称编造手柄或附件。
省略端部时自动入口默认 free/free；上例的 world_fixed 是示例明确选择的悬挂
条件，不能仅凭“电线”或“带手柄”擅自采用。

使用调用者已经选定且配套安装的两个 Python：

```powershell
& $PhysicsPython -I -B -m sca_physics configure-cable $RawPackage `
  --target $ExactCableNodeId --chain-id cable --config $RequestJson --output $FinalPackage
& $PhysicsPython -I -B -m sca_physics validate $FinalPackage
# 前面每个命令已经退出。下面只传最终产品包。
$FinalSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $FinalPackage).Hash.ToLowerInvariant()
& $UsdPython -I -B -m sca_usd_export export $FinalPackage $NewUsda `
  --expected-input-sha256 $FinalSha --require-physics
& $UsdPython -I -B -m sca_usd_export validate $NewUsda
```

自定义适用细长体改用完整声明入口，后续独立 validate/export 相同：

```powershell
& $PhysicsPython -I -B -m sca_physics set-rigid-chain $RawPackage `
  --target $ExactCableNodeId --chain-id cable --config $FullChainJson --output $FinalPackage
```

`$FullChainJson` 使用 `set-rigid-chain` 的完整格式，不是上例简写 request；其中
显式保存 N、世界 SI 中心线、截面、材料、端部、接触和求解参数。几何及用户值
保留真实来源，工程估计标 `estimated`；不能仅改外层 source 将所有值一概视为
已标定或用户明确值。缺少可靠选段依据时说明该 N 的选择与未验证范围，不宣称
通过这一手工入口获得通用自动策略。

输出路径必须不存在。Windows 经宿主调用时仍使用正式 `scripts/use.ps1` 的
字符串数组参数包装，以保留空格、中文和 shell 字符；不能把用户描述拼入
可执行 shell 字符串。完整配置写入 JSON，避免包装器长度限制。

语义 CAD Mesh 使用 float32 顶点，而不是无限精度坐标。现有默认几何绝对
容差为 `1e-8 m`；较长线缆或离原点较远的模型可能仅因顶点舍入超出它。
新的已解析线缆将 CAD 世界坐标尺度对应的一个 float32 ULP（至少 `1e-8 m`）
保存为 `resolution.solver.export_geometry_absolute_tolerance_m`。未明确覆盖
该配置时，导出器在网格计算前读取已保存的建议，多链取最大；不会重新推断
材料或段数。`geometry_tolerance_policy` 记录实际值、来源和各链建议。

用户显式 `--config` 中的 `geometry_absolute_tolerance_m` 优先，即使过小而
导致拒绝也不自动提高；不包含此字段的其他配置不阻止保存建议。直接 Python
API 的 `config=ExportConfig(...)` 默认视为完整显式几何配置；需要按字段
合并时可给 `export_package` 的 `explicit_config_fields` 指明实际显式字段。
旧包无此保存建议时仍使用原默认。这一几何舍入预算不修改材料、动力学门槛，
也不证明物理精度。

## 保存什么，导出时读取什么

Schema 1.6/1.7 的刚体链声明可包含 `resolution` 和 `resource_budget`。新的描述
配置流程保存已解析的类别/预设版本、用户意图、几何和参数来源、分段规则及
最终显式 `discretization.free_segments`、材料段长、求解建议和验证限制。

Schema 1.7 增加可重算的 `derived` 来源，保留规则、实际输入及公式；只有贴标签
而没有可核对计算的结果会拒绝。均质实心圆杆可使用 physics 的公开 `circle_rod`
换算 λ、EI、GJ；复合电线不能套用单一材料模量。需求、模型选择、估计假设和
分段理由随包保存。重配置保留未修改字段及原来源；Agent 估计不能静默替换已保存
的用户明确值。旧 Schema 1.0–1.6 按原合同读取，不补来源、不重新选段。

USD 导出仍使用已经保存的 `free_segments`、SI 中心线、整线等效 EI/GJ 和
线密度，按实际相邻材料长度换算关节参数。完整声明同时保存为 USD 根链的
`sca:physicsDeclaration` 字符串和报告的 `declaration`；报告的
`resolved_configuration` 逐项保留 `resolution`。独立重开会检查两份声明及
已解析记录一致。预设升级不能在 USD 阶段改写旧资产。

手动显式段数、目标材料段长、自动策略是互斥的选择方式，由 physics 在
保存时解析。已保存的旧包仍保持其原显式段数；重新打开或导出不触发重分段。
要重新推断，必须在 physics 阶段明确生成新配置和新产品包。

## 512 与场景预算

旧 Schema 1.4/1.5 的 512 来自本插件对的输入合同保护值；USD Capsule/D6
生成器没有以 512 为数组布局或后端索引上限。旧合同保持原有范围，新
Schema 1.6/1.7 通过显式资源预算处理更大的请求，超过 512 必须带预算。
这不承诺任意大链在任意硬件上的运行速度或数值精度。

`resource_budget` 的六个正整数是可覆盖的计算保护预算。1.7 使用有来源的 value
wrapper，旧 1.6 的裸整数仍可读取；不是材料属性或精度指标：

| 字段 | 导出前检查的量 |
|---|---|
| `max_bodies` | 场景已有活动普通刚体加所有线缆段刚体 |
| `max_joints` | 普通关节加所有内部 D6 和端部 FixedJoint |
| `max_colliders` | 普通碰撞体加所有线缆 Capsule |
| `max_filtered_pairs` | 普通碰撞过滤加所有链和附件过滤对 |
| `max_contact_pairs` | 全部碰撞体两两组合减显式过滤对的保守候选上界 |
| `max_solver_work_per_second` | 各链 `段数 × physics_hz × (position_iterations + velocity_iterations)` 之和 |

预算的 scope 是整个导出场景，多条链同时声明预算时取各项最严格值。其他旧链
和普通刚体也计入数量；work 只估计线缆关节求解负担，不含普通体、接触、渲染
或硬件吞吐量。contact 是保守组合上界，不是实测接触数或宽相位输出。

导出器先用标量检查刚体、关节、碰撞体和 work，再检查过滤及 contact 数量，
通过后才创建线缆 prim 或过滤关系。预算不足明确报错，调用者可以有意识地
提高预算或选择成本更低的配置；不会静默截断段数或降低物性。

相邻过滤和材料局部重叠过滤按材料坐标计算，与当前世界空间距离无关。局部
过滤使用按半径分组的有界搜索，不再对所有 link 两两检查。关闭 self collision
仍要生成 `N(N−1)/2` 个过滤关系，必须先检查相应预算，不能把关闭碰撞误当成
零导出成本。远弧长折回的线段保持所声明的自碰撞能力。

报告的 `scene.rigid_chain_resources` 及各链 `resource_usage` 记录预算、估算量
和结果。正式 validate 从持久化声明反算并核对这些记录；`passed=true` 只表示
资源保护检查通过，不是物理精度或原生运行性能通过。

## 求解与验证边界

类别默认求解设置是有预算的工程起点，应与实际验证范围一同交付。第一阶段
16384 Hz/P255/V0 是精细对照，不能统一作为普通资产默认；原480 Hz也有明显
失准证据，不能因保存或导出成功宣称其普遍可靠。

区分物性、求解配置和检查判据：EI/GJ、线密度、阻尼及接触描述所建模型；Hz、
迭代数和时间预算决定计算方式与投入；检查判据定义某次测试的观察与停止条件。
资源预算通过、静态几何容差通过或某个求解设置合法，都不能替代物理真实性依据。
若需要研究求解误差，保持被研究的物性、载荷和边界可比，记录数值设置变化；
不要为使检查通过悄悄改变材料。需要新的物理决策时在 physics 保存新包，不在
USD 导出后补写关键配置。

当前模型为圆截面、平面初态、直线自然态的 Capsule/D6 链。平移锁定对应
不可伸长近似，没有通过无效字段实现有限抗拉刚度、断裂或塑性。初始曲线会
相对直线自然态带弹性预载。EI/GJ 是整线等效参数；多股电线不能简单用纯铜
或护套的单一材料模量代替。逐关节阻尼不代表分段无关的连续分布阻尼。

原 CAD 目标保留为隐藏语义 Mesh；动态可见形状和碰撞体均为逐段 Capsule，当前
显示色固定深灰，不是沿原 CAD 材质连续变形的 skin。材料弧长用于质量分配，
Capsule height取弦长且半球帽伸出材料站点。过滤策略用于处理局部重叠，仍会影响
接触范围；颜色、连续外观、端部接触、孔腔及插拔效果需要按实际代理分别检查。
普通刚体的 convexHull 碰撞还可能填平孔槽。HTML preview只绘 Mesh，不绘 Capsule，
也不执行接触；它不能单独验证动态链外观或碰撞正确性。

空间曲线初态、曲线自然态、分布阻尼、任意实体中心线反求仍不支持。几何
自动读取只在 physics 明确支持且核实的几何类型内工作；不把任意 BREP 的
包围盒当作已提取的真实中心线。

分别报告生成/保存/导出、USD 结构和值检查、有限原生运行，以及工程精度和实物
标定的证据范围。历史失败保留原工况和量值，是否影响当前任务结合用途、表示和
判据依据判断。没有理论或实物依据时，不以一次短运行声称真实电线或甩绳运动已
准确还原；也不把某个工程守卫的越线直接升级为物理错误。

## 可选的有预算原生运行

安装 payload 自带 `scripts/cable_runtime_smoke.py` 和外层 `scripts/run_cable_runtime.py`。
用外层入口启动调用者明确提供的 Isaac Python；导出环境本身不需要安装 Isaac：

```powershell
& $UsdPython -I -B "$InstalledUsdAddon/scripts/run_cable_runtime.py" `
  --isaac-python $IsaacPython --usd $NewUsda --report $NewReport `
  --chain-id cable --output $NewRuntimeResult --duration 5 --max-duration 5 `
  --max-steps 6000 --mode task --guard-action record --task-criteria $CriteriaJson
```

该示例预算适用于不超过 1200 Hz 的 5 秒任务；预算应根据资产保存的 Hz 显式决定，
不是通用推荐求解设置。未指定时仍保留旧的 0.25 秒请求、0.5 秒和 2048 步上限；
超预算在启动 Isaac 前拒绝。`--duration` 与 `--steps` 互斥。输出目录必须不存在。
物理包旧记录中的 `finite_trial_budget` 是历史工作建议，不是跨调用计数器或通用验收上限。

`LIMITS` 的原数值未改：关节间隙 0.5 mm、线速度 10 m/s、四元数范数误差 1e-4、
链段质量/惯量对角项相对误差 1e-4。默认 legacy 模式仍按原条件停止并保留原 `passed`。
在运行前明确选择的 task/record 模式中，速度、间隙等工程警戒记录为事件并继续观察；
非有限状态或无效姿态属于运行异常，质量/惯量不一致属于配置一致性错误，仍停止。
原始警戒事件和 legacy 判定不被改写，任务验收单独记录。

10 m/s 来自历史助手设置的工程停测线，没有材料或动作标准支持其作为跳绳速度上限。
越线只能确认警戒触发，不能独立断言物理有错；记录后继续的策略须在启动前选择。
各事件的实际动作、自由有效长度定义、弯曲观察及判据格式见
[任务运行与证据核对](runtime-task-validation.zh-CN.md)。任务判据要说明适用依据；
预登记本身不证明力学准确，连接门槛也不是材料标准。

检查器不构链、不修改材料或求解设置、不事后写 USD。它读取真实 tensor 位姿、
速度、质量和惯量，按真实局部锚点计算间隙，记录自由材料段有效长度和末端弯曲时序。
保存 registration、census、逐步 NPZ、metrics、summary 及源层 SHA。
外层入口独立核对实际完成步数、积分时长、摘要、原始轨迹和源层哈希，再输出
`assessment.json` 与退出状态；不能只相信 Isaac 的进程退出码或旧 `passed`。
不回读完整关节驱动冲量，不宣称完成力矩平衡、稳态、材料标定或真实甩绳验证。

## 尚未实现的能力

- 原 CAD 材色沿链连续变形的动态 skin：当前原 CAD 隐藏，动态显示和碰撞仍为深灰 Capsule。
- 空间曲线初态、曲线自然态、有限抗拉伸长、连续分布阻尼及任意实体的可靠中心线反求。
- 根据运行误差自适应选段：预设辅助算法是初始工程规则；自定义路径由 Agent 决定显式 N。
- 任意分段和时间步的精度保证。旧 480 Hz 大链失败与精细对照证据继续保留，不因新来源合同而解决。

FEM 的 480 Hz 仍是当前导出代码对 `physx_deformable_volume` 的场景策略，来源为
历史案例/viewer 假设；它不适用于按保存 `scene.physics_hz` 导出的刚体链，也没有
因本次修正变成 FEM 可任选步频。两条路线的实际求解/接触证据须分别判断。
