---
name: sca-usd-export
description: 用户要求把 SimpleCADAPI 已生成的 .scadpkg 导出到 OpenUSD，或检查导出的 USD、物理描述、建模历史和预览时使用。读取产品包的定义、实例图、已计算 BREP、连接器、约束、特征图及根定义 metadata.sca_physics，输出 .usda、逐项物理报告、历史资源和可选 HTML。物理补充优先使用 sca-physics 技能。
---

# SimpleCADAPI 到 OpenUSD

本候选 add-on 名为 `sca-usd-export`，版本 `0.1.8rc2`，物理能力可选配套 `sca-physics==0.1.8rc1`，当前声明支持 Windows x86-64、Python 3.12、SimpleCADAPI `>=2.2.5,<2.2.6`。没有 `metadata.sca_physics` 的包可在未安装物理插件的环境导出；含该 metadata 或使用 prepare-deformable 时必须加载匹配物理实现。provision前设置 `SCA_PHYSICS_SOURCE` 才准备配套能力，未设置则准备独立几何环境。缺失/错误版本明确拒绝，不降级丢弃物理数据；依赖和升级边界见 payload 的 `docs/release-0.1.8rc2.zh-CN.md`。
它消费完成的产品包；不注册 `sca export --format usd`，不改变主 SCA 命令或建模环境。

实测配对仅physics0.1.8rc1+USD0.1.8rc2，环境Windows x86-64、Python3.12.15、SDK2.2.5、OpenUSD26.8、OCP7.9.3.1。所选2026-10-19 physics0.1.0/schema1.0、2026-10-20 physics0.1.3rc1/schema1.1包及旧路径/内存API通过，完整输入哈希见payload的 `docs/compatibility-0.1.8rc2.zh-CN.md`。**旧无来源裸参数原样不兼容；按要求补充来源信息后，所测试案例通过。**补正确单位和真实`source.kind/detail`，资料附citation/conditions；可运行例子为配套physics的 `examples/pendulum/physics-input.json`，不得复制成虚构实测出处。

无物理metadata用户仅升级USD；已有physics0.1.8rc1可保留，其他物理用户先准备该准确版本，再更新USD/指定源provision/check，最后切换新包写入。Python的`physics` extra是可选运行依赖，`test` extra另含pytest/jsonschema/physics，hatchling是构建依赖，Isaac是外部仿真环境。USD物理路径确实直接调用physics模块，非仅JSON互通。短安装根实测工作区下 `personal-workspace/results/arp-20261025-162555/s`、`p`，home为`s/a`、`p/a`；新安装选新短根。两项外部FEM检查、Isaac／PhysX行为、其他环境和全部历史版本组合未验证，不能由安装和静态导出推断原生仿真全面通过。

接受 metadata Schema 1.0–1.8；1.8 增加普通关节物理/执行器、articulation 和最小角到线 Mimic 传动。1.6/1.7 读取已保存 resolution 和资源预算，不重解预设或段数。1.7 增加严格来源和可重算派生记录。旧合同：1.5 仅将刚体链 scene.physics_hz 上限扩至 16384，1.4 仍限 4096。最终包由配套 physics 严格校验，不升级旧包、不裁剪高 Hz 值；旧 1.4/1.5 分段 2..512 保持，1.6/1.7 允许明确预算下更多段，solver iterations 仍不超过 255。高 Hz 可导出不等于力学精度通过。

## 机械臂普通关节与主动驱动

先由配套 physics 通过公开 enrich/set-joint 保存稳定 joint ID 的 motion/limits/max_velocity/friction、完整 drive 和 assembly.articulation，再从最终包独立进程导出；细节见[普通关节执行器](references/joint-actuators.md)。不在仿真脚本临时补建关节、DriveAPI、articulation、初态或关键参数。模型缺失硬件限位/电机数据时，把试验参数标记 estimated 并说明用途；执行器上限不等于电机额定力矩。

SI声明：旋转坐标rad、角速rad/s、K为N*m/rad、D为N*m*s/rad、max_force为N*m；移动为m、m/s、N/m、N*s/m、N。角USD目标/限位乘180/pi，角K/D乘pi/180，再按舞台长度平方换算。主动与被动共用force Drive：D=Dp+Da，有效vtarget=Da/D*vtarget；cap限制组合力，off保留纯被动旧行为。joint friction与接触摩擦/刚体角阻尼/Drive阻尼各有语义，不能替代。

fixed_base=true 导出原接地簇为 anchored rigid root link，通过 world FixedJoint 固定并在该joint应用RootAPI，保留原CAD fixed分组。非零初态写JointStateAPI，实际link/DOF/父子拓扑仍须Isaac核实。max_velocity/friction要求articulation启用；不连通、闭环、kinematic链接或无效根明确拒绝。Mimic只支持同一articulation中limited旋转reference→limited移动follower，方程qf+g*qr+offset=0，g单位m/rad；follower无独立Drive且被动D=0，保存真实来源和适用范围，不能当作齿面接触仿真。

运行时使用 `from sca_usd_export import update_drive_targets`，以稳定joint ID发送 `target_position`/`target_velocity` 的SI值；helper读取已导出模式、舞台单位及组合权重，只更新既有目标，不写Transform或JointState。新进程回读/导出的USD、静态核对、真实仿真和工程标定分别报告。

## 决策、来源和最终资产保护

Agent 判断材料、模型和计算预算；插件只执行、校验和保存，不调用在线模型服务。
提交的有效参数优先于 evidence、预设和默认。逐值来源优先于本次组/声明、再到本次根来源；仍缺来源的显式值报完整字段路径并拒绝。程序补全值自行标记 default，不能继承用户标签。Agent 估计标 estimated，资料标 reference 并注明 citation/conditions；不要把推断标作用户明确输入。derived 保存固定规则、实际输入及公式，须重算核对。

明确选择匹配预设，或使用完整 set-rigid-chain 自定义声明及显式 N。Agent 可依据任务决定 N，插件原样采用合法值；未知描述不会默认成电线。均质实心圆杆可用配套 physics 的 circle_rod 换算，不能将其套到复合电线。重配置保留未改值和来源，估计不能静默替换已保存用户值。旧包导出不补造来源或重推 N。

新流程必须使用 export 的 `--expected-input-sha256 SHA256 --require-physics`，仅传选定最终包；核对 report 的包哈希及物理 metadata 哈希，并检查导出后包字节不变。两个选项对旧调用保持可选。细节见安装 payload 的 `docs/agent-decisions.zh-CN.md`。

历史验证与已知限制见安装 payload 的 `docs/VALIDATION-0.1.6rc1.zh-CN.md` 和 `docs/RELEASE-NOTES.zh-CN.md`。旧工况和失败记录保留，不能由软件测试数量覆盖；其适用性应结合当前对象、动作和用户目标判断，不预设新的任务必然通过或必须失败。

## 选择配置路径并消费最终包

先识别用户用途、真实几何、端部条件和已有物性证据，再决定现有表示是否适用。能用可逆假设推进时说明假设；缺失信息若会改变对象类别、边界条件或交付用途，再作有针对性的确认。已有 CAD 的尺寸和连接不因预设方便而改写。

- **匹配现有类别预设**：普通多股软电线或 PVC 跳绳与预设假设相符时，由配套 physics 的 `configure-cable --config request.json` 保存配置。Agent 在 JSON 显式写 `category`（`stranded_wire` 或 `pvc_jump_rope`），核对来源和适用范围；省略或未知类别明确拒绝。
- **自定义但适合当前链模型的细长体**：使用 `set-rigid-chain --config chain.json` 的完整声明和显式段数，提供真实世界 SI 中心线、截面、端部和参数来源。可按用途提出有依据的工程估计并标 `estimated`；该入口没有通用自动选段或任意材料推断能力，不必为获得自动配置强行套入两种预设。
- **模型不适用**：说明当前表示缺少什么能力及对结果的影响，不把对象强归为电线或 FEM。其他既有表示只有在满足各自几何、网格、附件和后端条件时才使用；缺失能力不能靠改标签、补假字段或改 USD 绕过。

已有有效最终包可直接 `export final.scadpkg scene.usda`。USD 不重猜 EI/GJ、重新选段或补链；需要新的物理决策时回到 physics，使用其已有修改入口保存新包。详细输入与决策边界见 payload 的 `docs/automatic-cables.zh-CN.md`。

包内存在 resolution 时，检查 report 的 `resolved_configuration` 完整对应保存记录；含资源预算时再核对相应 `resource_usage`/场景资源记录。手工声明和旧包可合法没有这些可选记录，不因此判异常。资源量是场景保护估算，不是物理准确或计时保证。超预算报告具体项目，不能裁剪N或偷偷关碰撞。旧显式N和旧Schema不自动改段。

新版资产无显式config时可消费保存的float32几何舍入建议；显式容差优先，失败不能通过放宽机械门槛掩盖。记录geometry_tolerance_policy的实际值和来源。该几何舍入预算与物理精度不同。

有 Isaac 且用途需要时，按待验证的问题选择动作、观察量和有限运行预算。payload 的 `scripts/cable_runtime_smoke.py` 只做初始化和保持观察，不施加额外载荷；默认请求0.25秒，默认上限0.5秒/2048步，可通过已有 `--duration` 或 `--steps` 及 `--max-duration`、`--max-steps` 明确设置更长检查。代码仍在 `resolution.solver.finite_trial_budget` 保存“2次、每次0.5秒”的遗留建议；它不是跨调用次数计数、通用验收或用户任务上限，重导时保留该记录，不以文档调整为由修改旧包。

保留固定 LIMITS 及旧 legacy 停测判定；10 m/s 是工程停测线，不能仅凭越线判定物理错误。新任务用 payload 的 `scripts/run_cable_runtime.py` 外层启动器，在运行前明确选择 `--mode task --guard-action record` 及有依据的判据；该模式保留警戒事件并继续，运行异常和配置不一致仍停止，任务验收另列。父进程核对 summary、NPZ、实际步数、积分时长与源层哈希，输出 assessment，不能以 Kit exit0 或旧 passed 代替任务通过。未运行就标未运行，不自动改 simulator_validated。格式见 payload 的 `docs/runtime-task-validation.zh-CN.md`。

## 运行前检查

先核实用户选定的安装与解释器，通过正式 dispatcher 检查 payload/runtime 文件集合、SHA256、模块位置、解释器和已启用的配套 physics；同版本号不足以证明同一代码。仅更新 payload 后旧 runtime 必须拒绝，需从当前 payload 重新 provision。独立候选也先作此身份检查，再使用已核对的 Python。无 peer receipt 的环境若发现遗留 physics 也拒绝，应明确提供配套源或另选全新几何 runtime。
已有明确候选环境时继续使用它，不改用默认 stable，也不自动升级或重建环境。
下面的宿主 addon 命令适用于用户选择通过宿主登记调用的情况。

1. 使用用户选定的宿主 SCA 命令运行 `sca addon check sca-usd-export`；自定义安装位置时同时提供对应 `--home`、`--skills-dir`。
2. 检查失败时报告缺失的运行环境并停止导出。运行环境准备入口是 `sca addon use sca-usd-export provision`；它在独立 `runtime_dir/.venv` 中安装依赖，不继承宿主建模环境的包。
3. 可以用 `sca addon use sca-usd-export` 查看实际 `addon_dir` 和 `runtime_dir`。代码属于安装目录，虚拟环境和缓存属于运行目录，导出文件由调用者选择。
4. 运行环境准备后再次执行检查，不用安装时缓存的检查结果代替当前检查。

SCA 2.2.5 管理命令的 exit0不能单独证明 probe 成功；检查返回或注册记录中的真实 `runtime_check.passed`。

宿主 SCA Python 仅运行 SCA CLI；USD、SCA 读取器、OCP、NumPy 等实际导出依赖安装在 add-on 自己的环境。
`pyproject.toml` 固定 `cadquery-ocp==7.9.3.1`，与原实现已测内核版本一致；准备环境可能下载较大的二进制依赖包。
如需指定用于创建环境的基础 Python 3.12，设置 `SCA_USD_BOOTSTRAP_PYTHON` 为其可执行文件路径。
未指定时准备脚本尝试 `py -3.12` 和基础 `python`，并校验版本。

## 安全传参和调用

SimpleCADAPI 2.2.5 在 Windows 的 `addon use` 实现将 POSIX 风格引用交给 `cmd`。
带空格、中文或 shell 字符的文件路径不要直接拼入其数据命令字符串。
使用已安装目录的 `scripts/use.ps1`，传入宿主 `-ScaPython`、安装位置 `-AddonHome/-SkillsDir`，以及真正的字符串数组 `-Arguments`。
包装器将参数数组编码为 UTF-8 JSON 的 Base64，经 `argv-json-base64` 入口传入独立 Python；不把数据解释为代码。
安装代码和运行环境的根路径不能含单引号或 `cmd` 元字符（如 `& | < > ^ % ! ( )`）；空格可用。这是 SCA 2.2.5 Windows 启动链的限制。
Windows 未启用 Long Paths 时另选较短 state/runtime 根；本机深层安装实际在 pip 解包 Jedi 类型桩时触发 WinError206。日志可留主结果目录，不改 SDK 或系统注册表来掩盖失败，provision 与 probe 均须真实通过。
模型输入、输出和配置路径不受此限制，可以含空格、中文、单引号和 `&`，但必须通过包装器编码传递。
PowerShell 传输层限 1～256 个参数，Base64 token 最长 6000 字符；Python 内部虽允许 64 KiB，外层 `cmd` 容量更小，仍须遵守传输层限制。
超长配置写入 JSON 并传 `--config` 路径；不能拆分 token 或退回拼接原始 shell 数据。

支持的参数数组形式：

- `@('export', inputPackage, newUsdaPath)`；可附加 `--config` 和 JSON 路径、`--up-axis Y` 或 `--up-axis Z`。
- `@('prepare-deformable', inputPackage, exactTargetNodeId, newSurfaceJsonPath)`；可附加 `--config`，输出所选 CAD 实例的闭合世界米制表面，供 Isaac 官方体网格构建使用。
- `@('validate', usdaPath)`：重开 USD，并读取相邻报告与历史。
- `@('history', historyManifest)`：读取历史清单；附加 `--definition` 和定义 ID 可读取特征图。
- `@('preview', usdaPath, newHtmlPath)`：创建离线几何预览。
- 现有 HTML preview 仅绘制三角 Mesh，不绘制新的 Capsule 刚体链；检查链使用 USD/Isaac 和正式 validate，不能由 HTML 中缺线缆判断模型缺失。
- `@('check')` 和 `@('provision')`：检查或准备独立环境；这两个固定单词也可直接传给 `sca addon use`。

所有输入、输出、配置路径按调用者当前目录解释；优先使用绝对路径。输出已经存在时换新名称，不覆盖。
安装与调用细节见 add-on 安装目录中的 `README.zh-CN.md`；不能把此技能副本所在目录误当作代码安装目录。

## 临时配置验收

常规 `sca init/addon add` 是真实用户安装，会写用户 `~/.sca` 配置及所选登记、代码和技能目录，即使使用自定义 `--home/--skills-dir` 也不能称作临时配置运行。
复现本地安装检查时使用代码仓库的 `scripts/acceptance.py --sca-python HOST --bootstrap-python PYTHON312 --package PACKAGE`。
该工具将子进程用户目录、SCA 状态、独立环境和导出产物隔离到新工作目录，不写真实用户配置，不更改宿主依赖。
未给 `--work-dir` 时自动创建临时目录；显式路径必须尚不存在。保留 `acceptance.json`、运行环境记录和步骤日志作为证据。
只有修复先前失败后才使用 `--work-dir 原失败目录 --resume` 更新并继续；已有 USD 仍不覆盖，失败前若已产生输出则使用新的工作目录。
不预写任何通过结论，也不把本地安装检查描述为 GitHub 安装或 Isaac Sim 验证。

## 产品包消费约定

先使用主 **SimpleCADAPI** 技能建模、求解并 `capture` 出 `.scadpkg`，再调用本 add-on。
完整产品包说明见主 SimpleCADAPI 技能发布的 `references/scadpkg-format.md`；规范 JSON Schema 随 `simplecadapi` wheel 位于 `simplecadapi/contracts/`。
通过 SCA 的验证读取器读取定义、`occurrences/root.json`、BREP 缓存、材料、连接器和特征资源；沿清单摘要解析资源，不猜文件名。
当前不要求任何 `interface.*` 标签；缺少约束或连接器时不能从 STL、网格连通性或几何外形编造部件和铰链。
STL 不参与本导出路径；输入必须是 `.scadpkg` 或受支持的内存产品包。

源定义长度单位为毫米，顶点和局部平移各转一次 SI 米；USD 质量单位为千克。
默认 USD `metersPerUnit=1`。候选可通过导出 JSON `{"meters_per_unit":0.01}`
显式选择厘米存储；插件会转换所有已支持的维度属性，声明和报告仍为 SI，
不能只改舞台单位标签或给根节点加缩放。详细合同见安装 payload 的
`docs/cad-deformable.zh-CN.md`。
优先读取根定义 `metadata.sca_physics` 的完整物理配置。先完成 SCA capture，再按 `sca-physics` 技能补充产品包、检查最终包，再调用本插件导出。重新建模/capture 后必须重做物理补充。
包内 `scene.up_axis` 决定 USD 向上轴和相反方向的重力；不旋转网格、装配、锚点或轴。旧包无扩展时使用追踪到报告的 +Z/0.5/0.4/0.1 演示兼容值并警告。
仅用户明确要求时用 `--up-axis Y|Z` 覆盖，并记录前后值；不要自行传该选项覆盖包配置。含扩展时 `--config` 的物理字段会报错，应修改补充输入并生成新包。
按需阅读代码仓库的 `docs/PHYSICS.zh-CN.md`，包括 SI 阻尼单位换算、被动零刚度零目标 Drive、碰撞过滤、来源及不支持情况。

当前版本保留 schema 1.0–1.3 的后附加流程、schema 1.4/1.5 的 `rigid_joint_chain` 和 1.6/1.7 保存结果；不依赖实验版 0.2 的 PhysicsDocument/authoring 入口。保留的刚体可选字段为 `parts[node_id].rigid_body.{mode,gravity_enabled,linear_damping,angular_damping}`、`collision.{contact_offset,rest_offset}`、`contact.{friction_combine_mode,restitution_combine_mode}`。单位依次为 token、boolean、1/s、1/s、m、m、token、token；combine 只接受 average/min/multiply/max。通过配套 physics enrich 保存后再导出，不在 USD 阶段临时补用户物理值。

mode 省略时沿用旧静态/动态推断；明确 static/dynamic/kinematic 时核实原生 ground/fixed 关系，禁止把已接地组改成动态或运动学。固定合并组各成员的刚体属性若冲突必须报错。同一零件定义的多个实例仍各自拥有最终绑定的物理材料，实例参数不写共享几何 prototype。

除 mode 外，新字段可用 `{mode:"engine_default",unit,source}` 请求引擎缺省，不能同时带 value；省略不注入新默认。静态显式重力/刚体阻尼无效，必须报错；静态 engine_default 只记录 not_applicable，不写 API/属性。运动学刚体可携参数，但重力/阻尼不产生动态积分效果，不描述成已驱动运动。正 rest 必须配显式 contact 且 rest<contact；负 rest 合法。不要降低偏移、摩擦或放宽验收阈值来掩盖碰撞问题。

报告 `physics.optional_properties` 区分 authored、engine_default、omitted、not_applicable，保留单位/来源及 USD authored 状态；`kinematic_body_count` 与 dynamic/static 互斥。Physx offset 默认是 -inf 后端选择哨兵，不能记录成固定数值默认。独立 usd-core 的 API token/类型/值验证不等于 PhysX 采用默认或真实运动生效，运行结论仍需真实 Isaac 证据。

## 结果解释

- 保留定义、实例 ID、名称、层级及已求解初始位置；共享定义通过 USD 内部引用复用。
- 显式固定关系决定刚体分组；独立部件可以属于同一刚体。旋转和滑动关系使用真实连接器轴、锚点和限位。
- 普通刚体密度优先取 SCA 材料；可变形 CAD 目标使用其柔体材料密度，不写该目标的刚体质量或 CAD 密度覆盖。缺失密度、摩擦和碰撞近似等配置必须报告为配置或默认值，不能称作测量数据。
- 历史目录保留原始定义、特征图、操作参数、依赖及已捕获源码。读取历史不会执行建模；USD 不能因此执行拉伸、切除或倒角。
- 完整 CAD 恢复或装配重放仍需原始 `.scadpkg`；历史目录不复制 BREP、材料和拓扑缓存。
- 几何参数变更、重建几何及修复建模问题回到主 SCA 流程，重新求解和捕获，再导出。同一目标的后附加材料变更使用下述接口，无需重写建模程序。

分别报告写出成功、几何与静态物理描述检查、实际仿真器验证。初始化检查只核对后端注册、身份、质量/惯量和有限状态；行为验证要有对应任务的载荷、动作、连接与接触观测；精度验证另需相应数值收敛、理论或实物参照。延长 smoke 时间不能替代后两类证据。
没有运行 Isaac Sim 时，始终保持 `simulator_validated=false`，不承诺碰撞生成、机器人接触或推门结果。
若关节或装配出现错位，核对真实连接器、锚点、轴向及单位，结合当前输入定位原因，不自行调整几何掩盖问题。
GitHub 安装流程目前未验收；已声明的平台和版本范围不代表其他组合也受支持。

## 通用刚体关节线缆

对后附加 `rigid_joint_chain` 使用 [刚体关节链说明](references/rigid-joint-chain.md)。先由配套 physics 按适用路径使用 `configure-cable` 或 `set-rigid-chain` 保存完整中心线和物理配置，再独立回读并正常导出最终产品包；不要在 USD 后调用 V6 脚本添加刚体或关节。当前仅明确中心线的圆截面、直线/平面初始形状、直线自然态和逐关节阻尼。EI/GJ 和质量按局部材料弧长换算。非平面空间初态、无应力曲线自然态和分布阻尼不支持时必须报错。插件保存参数来源，不自动完成实物标定。

动态可见形状和碰撞均为分段 Capsule；原 CAD 目标作为隐藏语义几何保留，不由此得到连续变形皮肤。当前 Capsule 使用固定深灰显示色，不能宣称原 CAD 颜色或连续外观完整传到动态链。胶囊高度取中心线弦长，端帽向外延伸；质量按材料弧长分配。代理形状、局部过滤和接触设置会影响接触结果，显示完整或静态重开通过不能证明孔腔、复杂接触或真实插拔正确。

## 原生 FEM 候选扩展

保留 capture → enrich → 保存后独立回读/validate → export；不调用 0.2.0 authoring 框架。schema 1.2 的 cables 声明由候选配套插件处理，参数与限制见 payload 的 `docs/native-cables.md`。几何网格/原生材料/双向端点附件写入 USD，求解交给 Omni PhysX。仅 GPU dynamics 支持；材质 static_friction 和附件 stiffness/damping 不支持并拒绝输入。用户数值、0/false、SI 单位和来源必须持久化；未知字段报错。USD 静态重开不等于 Isaac 仿真成功。

对本节 `physx_deformable_volume` 原生 FEM 表示，当前导出代码仍显式保存 physxScene:timeStepsPerSecond=480（uint，目标 schema fallback=60）。这是继承的案例/viewer场景策略，未做通用时步收敛标定；本说明没有使其变为可任选配置。刚体链使用其已保存的 `scene.physics_hz`，不套用这一480Hz策略；无 cables 的旧资产不写该线缆场景项。GUI使用已导出场景步频，验证器检查属性类型、数值及 authored 状态。

可选 `deformable.enable_speculative_ccd` 为严格布尔，映射本机 PhysxBaseDeformableBodyAPI 的 `physxDeformableBody:enableSpeculativeCCD`。原生后端根据速度动态调整 contact margin，是否启用依据实际接触需求判断。它不是 rigid-body CCD，也不实现自定义求解器。显式 false 原样保存；省略不补值、不写 USD opinion，保留原生 false 默认。选择及依据应保存在参数 source 中。

可选 `collision.attachment_filtering` 是 token 参数 wrapper，仅接受 `whole_body` 或 `endpoint_elements`（unit=token，保留 source）。省略不补入包，沿用既有整线与两个连接刚体组 pair-filter 语义，报告注明兼容来源；显式 whole_body 同义。endpoint_elements 使用原生 OmniPhysicsElementCollisionFilter，对该端真实刚体组的每个 collider 分别建立过滤器。旧 swept 网格按端盖与首个轴向 span 选面；官方生成网格按持久化的 SI `simulation_mesh.filter_length` 在参考中心线上投影选面，不复用旧索引。其余线段与插头碰撞。报告列出真实 surfaceFaceVertexIndices 行 ID、物理过滤长度和 collider 路径；禁止残留整线 FilteredPairs。volume–rigid 元素过滤受 107.3 支持，旧 whole-body 选择不是后端能力限制。实际接触效果仍须单独运行验证。

## 选择已有 CAD 实例并反复改材料

选择 `sca-physics inspect` 列出的精确 part occurrence `node_id`。相同 definition
的其他实例保持独立。先通过配套 `set-deformable --target ID --config declaration.json`
把 `physical_type=deformable_volume`、实际 CAD 来源、官方生成网格、参考形状、
材料与附件保存进同一完整产品包；`--cable-id` 可指定初始声明名，后续更新保留它。
`prepare-deformable` 只准备真实 CAD 表面，不在 portable Python 中运行官方 mesher。
官方构建完成后必须将结果、精确来源指纹和新节点索引回写产品包。

已有有效柔体目标后，可直接执行：

```powershell
& $PairPython -I -B -m sca_physics set-deformable final.scadpkg `
  --target $TargetNodeId --material material.json --output material-edited.scadpkg
& $PairPython -I -B -m sca_physics validate material-edited.scadpkg
& $PairPython -I -B -m sca_usd_export export material-edited.scadpkg `
  material-edited.usda --config export.json
& $PairPython -I -B -m sca_usd_export validate material-edited.usda
```

`material.json` 中 youngs_modulus、poissons_ratio、density 单位分别为 Pa、1、kg/m^3；每个值须提供真实来源 wrapper，例如 `{"density":{"value":1500,"unit":"kg/m^3","source":{"kind":"estimated","detail":"针对该任务的等效密度初值；未标定"}}}`。不可用裸数值冒充用户输入。它修改同一声明，保留已有阻尼和几何网格；不重新建模。
`export.json` 可为 `{"meters_per_unit":0.01}`。该单位下 E 的数值乘 `.01`，
密度乘 `.01^3`，长度和重力数值乘 `100`，质量与每秒阻尼不变。
可选 `deformable.sleep_threshold`、`settling_threshold` 单位为 m/s，
`settling_damping` 为 1/s；前两者也按长度单位转换。

几何、世界变换、单位、网格或参考几何配置变化会使缓存失效；拓扑变化后，
端点索引与观测映射必须一起重建。材料编辑不使有效网格失效。
所选 CAD 源实例保留隐藏语义几何，并链接到唯一柔体、真实 CAD skin 和 FEM collider。
原刚体声明须由调用者通过 `--convert-rigid` 明确移除；涉及该目标的原生
固定、接地和装配关节若不能保留语义则报错，不静默丢弃。

当前 CAD 合同仍针对有参考中心线、端面和两个刚体端附件的线缆体积；不宣称
支持任意实体的自动附件或交互选择 UI。portable 导出层直接写本机 107.3 官方
schema，原生 FEM、附件和显示蒙皮由 Omni PhysX 求解。独立回读、静态校验、
运行对象注册、无错误初始化和行为验收应分别报告；超过 5% 的行为伸长不能
单独证明官方接入错误，也不能将一次失败变成永久禁止材料修改。


## 保留的历史 rc2 弹性阻尼单位修正

`material.elasticity_damping` 的单位为 `1/s`，对应本机 107.3
`PhysxDeformableMaterialAPI.physxDeformableMaterial:elasticityDamping`。
依据：[107.3 原生 schema 文档](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/deformables_beta/physx_deformable_schema.html#physxdeformablematerialapi)。
rc1 将该字段标作 `1` 是单位合同错误。rc2 对显式 `unit:"1"` 报错，
不会静默换算、接收错误标注或改写原包。需要调用者在确认原数值含义后
另存 `unit:"1/s"` 的新配置，并按原 enrich → 独立回读 → export 流程生成新资产。
未带单位的标量简写仍按该字段既定 SI 单位解释；显式 `0` 保留。
这次修正不会将原数值 `.01` 乘除任何比例；USD 的数值映射不变。
所测已保存schema1.0/1.1包和旧API调用兼容；旧无来源裸参数须先迁移，范围见payload `docs/compatibility-0.1.8rc2.zh-CN.md`。这是独立合同修正，**不代表已解决拉伸、
传力、反转或动作目标失败**。
