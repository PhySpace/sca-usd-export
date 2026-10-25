# sca-usd-export

这是独立的 SimpleCADAPI add-on：读取已完成的 `.scadpkg`，逐零件生成 OpenUSD，并保留结构、物理描述和建模历史。
USD 输入直接来自产品包中的 BREP、定义和实例数据，不经过 STL。
当前维护候选版本为 **0.1.8rc2**；无物理配置的产品包可独立导出。读取包内 `metadata.sca_physics` 或准备柔体时，使用可选且准确配套的 **sca-physics==0.1.8rc1**。许可为 Apache-2.0，来源见 [NOTICE](NOTICE)、[LICENSE](LICENSE) 和 [源码来源](docs/PROVENANCE.zh-CN.md)。依赖、升级和兼容边界见 [rc2 发布准备说明](docs/release-0.1.8rc2.zh-CN.md)。

接受产品包内物理 metadata schema1.0–1.8；这不是 `.scadpkg` 容器版本范围，也不代表每个历史生成器都已实测。1.8 增加普通关节的有来源限位、速度/摩擦、主动 force Drive、固定/浮动基座 articulation、非零初态和最小角到线 Mimic 传动，见[执行器、单位与限制](docs/joint-actuators.zh-CN.md)。1.6/1.7 保存描述解析/自动选段结果和场景资源预算；导出只读最终N，不重新推断。旧合同：1.5 仅将刚体链 `scene.physics_hz` 合法上限提高至16384；1.4仍限4096，既有低Hz包不自动升级。旧1.4/1.5分段2..512保持；1.6按明确资源预算允许更多段，solver iterations仍不超过255。输入范围不代表任意线缆的工程精度通过。

实际验证配对为 **sca-physics 0.1.8rc1 + sca-usd-export 0.1.8rc2**，环境为 Windows x86-64、Python 3.12.15、SCA SDK 2.2.5、OpenUSD 26.8、OCP 7.9.3.1（NumPy 2.3.1、Plotly 7.1.0）。所选 2026-10-19 physics 0.1.0/schema1.0、2026-10-20 physics 0.1.3rc1/schema1.1 成品包，以及旧路径/内存 API、JSON ExportConfig 的范围见[兼容与升级矩阵](docs/compatibility-0.1.8rc2.zh-CN.md)。**旧无来源裸参数原样不兼容；按要求补充来源信息后，所测试案例通过。**该文档给出原包哈希、真实来源迁移和可运行示例；不虚构出处，也不自动改写旧包。

无嵌入物理配置的用户只升级 USD 即可；已有 physics0.1.8rc1 可保留它，其他物理用户需准备准确配套源，再更新 USD 并重新 provision，之后才使用新写入流程。两项外部 FEM 检查、Isaac／PhysX 行为、其他环境和全部历史版本组合均未验证；add-on 安装及静态导出通过不代表原生仿真全面通过。

新增 schema 1.4 的 `representation: rigid_joint_chain`：从最终产品包直接生成胶囊刚体、D6 关节、端部固定/世界固定/自由端与明确碰撞过滤，再独立重开检查。中心线、质量/弯扭刚度、阻尼、离散和接触参数都先由物理插件保存于包内；导出不导入 V6 项目，不读取旧 USD，不生成 FEM 中间资产。详见[刚体关节链合同、运行与限制](docs/rigid-joint-chain.zh-CN.md)。几何建模、轨迹和原生仿真由模型/项目负责。没有通用连续 skin、曲线自然态或分布阻尼；参数来源不等于实物标定。

版本号及变更边界见[版本说明（保留历史）](docs/RELEASE-NOTES.zh-CN.md)。

旧 schema 1.0–1.3 和原生 FEM 导出入口保留。旧 FEM 静态导出成功不能推出动态通过；暂停中的 0.2 同步声明框架不属于本维护源。当前版本验证以新隔离环境和相应记录为准，以下历史记录不作为本版本通过声明。

历史0.1.6rc1通过所记安装、导出/重开、V6与通用结构检查，USD软件160项覆盖通过；**当时定量分段一致性及扭转理论验收失败**，见[保留的rc1验证记录](docs/VALIDATION-0.1.6rc1.zh-CN.md)。上一阶段rc2隔离安装的软件174项通过，新增外力子步策略与高步频合同；原生力学须以独立对照验证，不由软件通过推定，也不能扩展为通用线缆工程精度声明。

rc2精细设置的受控三档原量值门槛已复核，但严格瞬时静态条件仍失败；5秒弯曲部分运动窗也失败，详见版本说明的条件表述。新外力策略也影响低Hz新导出的V6：原结构回归通过，动态轨迹有变化，不能声称原求解行为完全不变。

当前声明支持 Windows x86-64、Python 3.12、SimpleCADAPI `>=2.2.5,<2.2.6`。
导出代码和虚拟环境独立于主 SCA 项目；不修改核心源码、原有导出器或 `sca export` 的格式列表。
本说明给出本地安装流程，实际检查结果以运行输出为准；此版本的 GitHub 安装尚未验收。
物理配置由独立 `sca-physics` 插件补充到最终产品包。导出器通过其 Python 合同校验器及网格/预算辅助函数消费根定义 `metadata.sca_physics`，支持每实例接触材料、碰撞及排除、被动关节阻尼、场景方向和完整来源记录；因此物理路径不是仅凭 JSON 互通。工作流程与单位见 [产品包物理配置](docs/PHYSICS.zh-CN.md)。该可选本地依赖尚未发布；需要物理路径时，准备环境前将 `SCA_PHYSICS_SOURCE` 指向匹配仓库或 wheel。未配置该变量时，只准备独立几何环境。
原 2.2.4 环境的历史检查见 [2026-10-16 验收记录](docs/VERIFICATION.zh-CN.md)；其中旧默认值和环境记录只描述当时状态。本轮 2.2.5 的检查以新测试与报告为准。
实际新环境和 99 项测试结果见 [2026-10-17 检查](docs/VERIFICATION-20261017.zh-CN.md)。

保留的后附加流程为 capture → physics enrich/set-rigid-chain → 独立回读 → USD export/validate，不接入 0.2 声明框架。旧包不提供可选字段时保留原推断与属性缺省，不注入新演示值。字段、engine_default 和适用性见[物理配置](docs/PHYSICS.zh-CN.md)。历史 99 项记录只描述原版本。

## 自动线缆最终包

由配套physics `configure-cable`根据描述、CAD和版本化预设选择属性/N，USD只消费最终包。新场景预算在authoring前检查实际body/joint/collider/filter/contact/work估计，超预算明确拒绝。旧2..512是插件保护而非已证实后端链硬限；新版1.6允许显式预算下更多段，旧包不变。详见[自动线缆与独立导出](docs/automatic-cables.zh-CN.md)。无用户几何容差时，新已解析资产可以使用包中保存的float32舍入建议；显式config优先、旧资产默认保持。

可选Isaac有限运行工具属于本payload的`scripts/cable_runtime_smoke.py`，检查初始化和短时结构，不校准材料、不证明理论精度。

## 安装本地仓库

在本仓库根目录打开 PowerShell。以下路径来自当前仓库和你选择的宿主解释器，不绑定某台机器。
宿主解释器必须已经安装 SimpleCADAPI 2.2.5；它仅负责运行 SCA CLI，实际导出使用 add-on 自己的环境。

```powershell
$Repo = (Resolve-Path '.').Path
$ScaPython = (Read-Host '输入已安装 SimpleCADAPI 2.2.5 的 python.exe 完整路径').Trim('"')
$AddonHome = Join-Path $env:USERPROFILE '.sca/addons'
$SkillsDir = Join-Path $env:USERPROFILE '.agents/skills'

& $ScaPython -m simplecadapi.cli --version
& $ScaPython -m simplecadapi.cli init --home $AddonHome --skills-dir $SkillsDir --no-shell
& $ScaPython -m simplecadapi.cli addon add --home $AddonHome --skills-dir $SkillsDir $Repo
```

仓库目录名、描述文件中的名称和技能名称须统一为 `sca-usd-export`。
当前 SCA 2.2.5 Windows 启动链要求安装代码与运行环境的根路径不含单引号或 `cmd` 元字符（如 `& | < > ^ % ! ( )`）；可以含空格。
如果用户目录不符合此条件，请为 `$AddonHome` 选择符合条件的位置；运行目录由 SCA 置于其相邻 `runtimes` 中。
本轮本机未启用 Windows Long Paths；较深的安装/runtime 根会使 pip 安装 SCA 的传递依赖 Jedi 类型桩时触发 WinError 206。请选择较短的安装/state 根；日志和报告可放较深结果目录。不能把较深目录下的 provision 失败解释为已安装成功，也不要为此修改 SDK 或系统注册表。已实测短根为工作区下 `personal-workspace/results/arp-20261025-162555/s`（独立）和 `p`（配套），add-on home 分别是 `s/a`、`p/a`，runtime 分别在其相邻 `s/runtimes`、`p/runtimes`；新安装选择新的同等短根并保留旧环境。
`--no-shell` 不修改系统 PATH。安装时独立环境尚未准备，运行检查可能给出警告；准备后需要重新检查。
上述命令是**真实用户安装**，会创建或更新当前用户 `~/.sca/` 下的 SCA 配置，并写入所选安装目录的登记文件、代码和技能。
即使传入 `--home`、`--skills-dir`，也不要把这组命令当作不写用户配置的试验；临时配置验收入口见下文。
已有同名 add-on 应使用 SCA 的更新流程，避免重复安装。

## 准备独立环境并检查

环境位于 SCA 为本 add-on 分配的 `runtime_dir/.venv`，不在会被更新替换的代码目录中。
准备脚本使用基础 Python 3.12 创建隔离环境，然后安装导出器及其依赖，不继承建模环境的 site-packages。
`pyproject.toml` 将 `cadquery-ocp` 固定为 `7.9.3.1`，与原实现已测的几何内核版本一致，避免准备新环境时自动换用不同内核。
准备环境可能下载较大的 OCP、OpenUSD 等二进制包；这不是只复制几个导出脚本，需预留下载时间和磁盘空间。
可选环境变量 `SCA_USD_BOOTSTRAP_PYTHON` 指定基础 `python.exe`；不指定时脚本尝试 `py -3.12` 和 `python` 并校验。
网络较慢时，可把已从可信来源下载的依赖 wheel 放入单独目录，并用 `SCA_USD_WHEELHOUSE` 指定它；准备脚本将这些 `.whl` 作为 pip 安装输入，仍校验包版本约束，并把所有包安装到插件自己的环境中。
这不是完全离线安装开关；缓存未覆盖的依赖和构建依赖仍需下载。不设置该变量时正常从包索引安装。

```powershell
# 可选：已知道基础 Python 3.12 位置时设置；否则保留自动查找。
# $env:SCA_USD_BOOTSTRAP_PYTHON = '基础 Python 3.12 的 python.exe 完整路径'
# 仅无 metadata.sca_physics 的几何包：清除继承的配套源变量。
Remove-Item Env:SCA_PHYSICS_SOURCE -ErrorAction SilentlyContinue
# 要消费保存的物理配置或使用 prepare-deformable 时，改为设置此变量：
# $env:SCA_PHYSICS_SOURCE = (Resolve-Path (Read-Host '输入配套 sca-physics 0.1.8rc1 仓库或 wheel 的完整路径')).Path

& $ScaPython -m simplecadapi.cli addon use --home $AddonHome --skills-dir $SkillsDir sca-usd-export provision
& $ScaPython -m simplecadapi.cli addon check --home $AddonHome --skills-dir $SkillsDir sca-usd-export
```

只有当前检查成功后才进行导出。安装时显示的运行状态是缓存，不能代替重新检查。
SCA 2.2.5 的 `addon check` 退出码不能单独代表 probe 成功；必须查看返回的 `runtime_check.passed`，或核对登记文件：

```powershell
$Registry = Get-Content (Join-Path $AddonHome 'registry.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Registry.addons.'sca-usd-export'.runtime_check.passed) { throw 'USD add-on runtime_check 未通过' }
```

准备环境需要获取 Python 依赖；导出本身不需要启动 Isaac Sim。
`pyproject.toml` 的 `physics` extra 表达可选 Python 依赖；SCA provision 由上述显式源变量选用该能力，并记录 peer 文件身份。缺少它或版本不匹配时，含物理配置的输入明确失败，不静默降级。已配套的 runtime 不能仅清除变量就当作无物理插件环境；验证独立能力应另选全新隔离安装。

## 用包装器导出

SimpleCADAPI 2.2.5 在 Windows 的 `addon use` 中存在引用方式限制。
直接传入含空格、中文或 shell 字符的路径可能被错误解析，因此数据命令统一走 `scripts/use.ps1`。
它把字符串参数数组编码成 Base64 JSON，经 `argv-json-base64` 入口传入独立环境，不执行数据字符串。
这只是参数传输方式，不改变输入文件内容。
模型输入、输出和配置路径可以含空格、中文、单引号和 `&`；它们经包装器编码，不进入启动 shell 的命令文本。
PowerShell 传输层每次接受 **1～256 个参数**，编码后的 Base64 token 最长 **6000 个字符**，超限会报错。
Python 内部解码上限为 64 KiB，但外层 `cmd` 容量更小，不能据此绕过 6000 字符限制。
长配置应保存为 JSON 文件并用 `--config` 传路径；必要时缩短路径，不能把超长 token 拆开拼成 shell 命令。

下面使用已安装的脚本，当前工作目录可以与仓库目录不同。所有参数路径按调用者当前目录解释，建议提供绝对路径。
输出 `.usda`、同名报告或历史目录已经存在时会报错；重新运行请更换输出名。

```powershell
$Use = Join-Path $AddonHome 'sca-usd-export/scripts/use.ps1'
$Common = @{ ScaPython = $ScaPython; AddonHome = $AddonHome; SkillsDir = $SkillsDir }
$InputPackage = (Resolve-Path (Read-Host '输入已有 .scadpkg 的完整路径')).Path
$OutputUsd = (Read-Host '输入新的 .usda 输出完整路径').Trim('"')

# 本窗口需要允许运行本地 PowerShell 脚本；此设置不改变系统策略。
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& $Use @Common -Arguments @('export', $InputPackage, $OutputUsd)
& $Use @Common -Arguments @('validate', $OutputUsd)

$PreviewHtml = [System.IO.Path]::ChangeExtension($OutputUsd, '.html')
& $Use @Common -Arguments @('preview', $OutputUsd, $PreviewHtml)
Start-Process $PreviewHtml

$HistoryManifest = Join-Path ([System.IO.Path]::ChangeExtension($OutputUsd, '.history')) 'history.json'
& $Use @Common -Arguments @('history', $HistoryManifest)
```

向上轴优先使用最终包内 `metadata.sca_physics.scene.up_axis`，旧包兼容默认 `+Z`。只有用户明确要求覆盖时，在导出参数数组末尾加 `--up-axis Z`，报告将记录覆盖前后值：

```powershell
# 为另一个新输出名称执行：
# & $Use @Common -Arguments @('export', $InputPackage, $AnotherNewUsd, '--up-axis', 'Z')
```

这个选项只改变向上轴标签与重力方向，不旋转原坐标。长度从 SCA 毫米转为 USD 米；不会居中或猜测装配关系。
可附加 `--config` 和 JSON 路径覆盖集中配置；未知字段和无效值会报错，具体字段见 [config.py](src/sca_usd_export/config.py)。

## 用临时配置复现验收

[scripts/acceptance.py](scripts/acceptance.py) 用一次性的 Windows 用户配置目录执行本地安装、独立环境准备、检查及数据命令。
它重定向子进程的用户目录，把 SCA 配置、登记文件、技能、独立运行环境、输入副本、USD 和日志放入新的工作目录，不写真实用户的 SCA 配置，不更改宿主 Python 的依赖。
这与前面的真实安装流程不同；只想检查本 add-on 时可以直接运行本节，不必先执行真实安装。

在本仓库根目录运行，提供已安装 SCA 的宿主解释器、基础 Python 3.12 和实际产品包：

```powershell
$Repo = (Resolve-Path '.').Path
$ScaPython = (Read-Host '输入已安装 SCA 2.2.5 的宿主 python.exe 完整路径').Trim('"')
$BootstrapPython = (Read-Host '输入基础 Python 3.12 的 python.exe 完整路径').Trim('"')
$InputPackage = (Resolve-Path (Read-Host '输入已有 .scadpkg 的完整路径')).Path
# 几何输入省略 --physics-source，并先清除 SCA_PHYSICS_SOURCE。
# 配套物理输入则设置 $PhysicsSource，并在下面命令加 --physics-source $PhysicsSource。

& $ScaPython -I -B (Join-Path $Repo 'scripts/acceptance.py') `
  --sca-python $ScaPython `
  --bootstrap-python $BootstrapPython `
  --package $InputPackage
```

默认自动创建并保留临时工作目录。也可加 `--work-dir` 指定一个**尚不存在**且满足安装根路径限制的目录。
脚本尝试从与源码无关的当前目录使用相对数据路径，并覆盖包含空格、单引号和 `&` 的路径传输；随后执行导出、重开验证、历史读取和预览，以及拒绝覆盖、输入缺失的错误检查。
结果、每步退出码与日志写入工作目录的 `acceptance.json`；`runtime-facts.json` 记录实际独立解释器和依赖，具体以当次运行产物为准。
这里描述的是验收入口与检查内容，不代表已经通过，不能替代实际运行记录。

`--work-dir <原失败目录> --resume` 仅用于修复失败后，在同一临时配置中更新 add-on 并继续检查。
它不是重复验收或覆盖既有 USD 的方式；若失败前已经生成输出，请换新的工作目录重新验收。
该入口也不测试 GitHub 下载或 Isaac Sim，不能用于判断铰接错位已经解决。

## 从空白 CAD 运行最小线缆示例

示例几何与唯一配置放在配套物理仓库 `examples/rigid_chain/`，不在两个仓库重复维护。它只建立 250 mm、4 mm 圆线缆、提供中心线和参数并调用插件；不包含分段或关节实现。先按各仓 README 安装并通过检查，再运行：

```powershell
$PhysicsRepo = (Resolve-Path (Read-Host '输入配套 sca-physics 仓库根目录')).Path
$PairPython = Join-Path (Split-Path $AddonHome -Parent) 'runtimes/sca-usd-export/.venv/Scripts/python.exe'
$Run = (Read-Host '输入尚不存在的绝对结果目录').Trim('"')
New-Item -ItemType Directory -Path $Run -ErrorAction Stop | Out-Null
# CAD 的 scad.use 缓存留在结果目录，不写维护源码旁。
Copy-Item -LiteralPath "$PhysicsRepo/examples/rigid_chain/assembly.py", "$PhysicsRepo/examples/rigid_chain/cable.py" -Destination $Run
& $ScaPython -I -B -m simplecadapi.cli run "$Run/assembly.py" --out "$Run/raw.scadpkg" --no-cache
if ($LASTEXITCODE) { throw 'CAD capture failed' }
& $PairPython -I -B -m sca_physics set-rigid-chain "$Run/raw.scadpkg" --target 'node/round_cable_demo/wire' --chain-id wire --config "$PhysicsRepo/examples/rigid_chain/chain.json" --output "$Run/final.scadpkg"
if ($LASTEXITCODE) { throw 'physics attachment failed' }
# 退出前一个进程后只读取最终包，不再提供中心线或 chain.json。
& $PairPython -I -B -m sca_physics validate "$Run/final.scadpkg"
if ($LASTEXITCODE) { throw 'package validation failed' }
& $PairPython -I -B -m sca_usd_export export "$Run/final.scadpkg" "$Run/final.usda"
if ($LASTEXITCODE) { throw 'USD export failed' }
& $PairPython -I -B -m sca_usd_export validate "$Run/final.usda"
if ($LASTEXITCODE) { throw 'USD reopen failed' }
```

这是静态保存/回读/导出示例；实际动力学通过标准和结果另见对应原生仿真记录，参数不是实物标定值。

## 输出能保留什么

- `.usda`：零件定义、组件实例、名称、层级、初始位姿，以及碰撞、质量、刚体分组和已支持关节的描述。
- `.report.json`：SCA 来源、计算结果、配置默认值、缺失信息和各级检查状态。
- `.history/`：原始定义 JSON、特征图 ZIP 及已捕获源码；USD 通过相对资源路径关联它们。
- 可选 `.html`：离线几何预览，不执行碰撞或动力学。

现有 HTML `preview` 只绘制可见三角 Mesh，尚不绘制新的 Capsule 链；它不能作为链存在与动态行为的验证。刚体链的显示和碰撞形状位于 USD 中，使用 USD/Isaac 查看，静态以正式 `validate` 记录为准。

共享定义通过内部引用复用。固定连接的部件可以有独立组件节点而属于同一刚体，如门与把手。
明确的旋转和滑动关系使用真实连接器轴、锚点和限位；不会从几何外观补出铰链。
密度优先来自 SCA 材料，缺失时使用报告中的默认密度；摩擦、恢复系数和碰撞近似是配置，不是测量值。
动态凸包会填平孔槽，复杂耦合约束也有支持边界，均需检查报告。

历史保存和读取不等于几何重建。USD 不会执行拉伸、打孔或倒角；改变参数后应回到主 SCA 建模流程，重新求解、`capture` 并导出。
历史目录不复制 BREP、材料或拓扑缓存，完整 CAD 恢复仍需原 `.scadpkg`。
传输到其他机器时，保留 `.usda`、同名报告和 `.history/` 的相邻结构，并单独保存原始产品包。

## 安装结构与维护

| 文件或目录 | 用途 |
| --- | --- |
| `sca-addon.toml` | SCA add-on 描述、兼容范围、平台及运行检查入口 |
| `skill/SKILL.md` | 安装给智能体的消费约定、运行前检查和结果解释 |
| `pyproject.toml` | 独立 Python 包 `sca_usd_export` 的依赖和安装声明 |
| `scripts/provision.ps1` | 创建并准备 add-on 独立运行环境 |
| `scripts/dispatch.ps1` | 接收 SCA 的运行检查、准备及参数传输命令 |
| `scripts/use.ps1` | 面向用户的 PowerShell 字符串数组包装器 |
| `scripts/acceptance.py` | 在临时用户配置中复现本地安装、独立运行及错误处理检查 |
| `src/sca_usd_export/` | 配置、SCA 读取、USD 写入、物理、历史与预览模块 |
| `tests/`、`docs/` | 独立包测试及验收文档；以具体记录为准 |

SCA 更新 add-on 时会替换代码目录，独立运行目录保留。依赖声明改变后仍需重新准备环境并检查。
更新本地维护仓后，配套模式保持 `SCA_PHYSICS_SOURCE` 指向匹配候选；独立几何模式清除该变量并使用无物理插件的 runtime。执行 `addon update --home $AddonHome --skills-dir $SkillsDir sca-usd-export`，再 `addon use ... sca-usd-export provision` 与 `addon check`，不要 remove 后重装旧 runtime。源码变化不会自动更新已安装的 wheel。
当前没有自动注册 `sca export --format usd`，也没有自动修复或改造输入模型。
几何建模请使用主 SimpleCADAPI 技能；产品包规范见该技能的 `references/scadpkg-format.md`，Schema 随 SCA wheel 位于 `simplecadapi/contracts/`。


## 保留的历史 rc2 弹性阻尼单位修正

`material.elasticity_damping` 的单位为 `1/s`，对应本机 107.3
`PhysxDeformableMaterialAPI.physxDeformableMaterial:elasticityDamping`。
依据：[107.3 原生 schema 文档](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/deformables_beta/physx_deformable_schema.html#physxdeformablematerialapi)。
rc1 将该字段标作 `1` 是单位合同错误。rc2 对显式 `unit:"1"` 报错，
不会静默换算、接收错误标注或改写原包。需要调用者在确认原数值含义后
另存 `unit:"1/s"` 的新配置，并按原 enrich → 独立回读 → export 流程生成新资产。
未带单位的标量简写仍按该字段既定 SI 单位解释；显式 `0` 保留。
这次修正不会将原数值 `.01` 乘除任何比例；USD 的数值映射不变。
所测已保存schema1.0/1.1包和旧API调用兼容；旧无来源裸参数须先迁移，范围见[兼容矩阵](docs/compatibility-0.1.8rc2.zh-CN.md)。这是独立合同修正，**不代表已解决拉伸、
传力、反转或动作目标失败**。
