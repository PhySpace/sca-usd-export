# sca-usd-export

这是独立的 SimpleCADAPI add-on：读取已完成的 `.scadpkg`，逐零件生成 OpenUSD，并保留结构、物理描述和建模历史。
USD 输入直接来自产品包中的 BREP、定义和实例数据，不经过 STL。
版本为 `0.1.0`，许可为 Apache-2.0，来源见 [NOTICE](NOTICE) 和 [LICENSE](LICENSE)。

当前声明支持 Windows x86-64、Python 3.12、SimpleCADAPI `>=2.2.4,<2.2.5`。
导出代码和虚拟环境独立于主 SCA 项目；不修改核心源码、原有导出器或 `sca export` 的格式列表。
本说明给出本地安装流程，实际检查结果以运行输出为准；此版本的 GitHub 安装尚未验收。
本机已完成本地 add-on 安装、独立环境导出、重新读取与 78 项测试；版本、命令、结果和证据位置见 [本地验收记录](docs/VERIFICATION.zh-CN.md)。
没有运行 Isaac Sim。现有铰接示例错位是已知未解决问题，不能把它当作装配或运动验收通过的样例。

## 安装本地仓库

在本仓库根目录打开 PowerShell。以下路径来自当前仓库和你选择的宿主解释器，不绑定某台机器。
宿主解释器必须已经安装 SimpleCADAPI 2.2.4；它仅负责运行 SCA CLI，实际导出使用 add-on 自己的环境。

```powershell
$Repo = (Resolve-Path '.').Path
$ScaPython = (Read-Host '输入已安装 SimpleCADAPI 2.2.4 的 python.exe 完整路径').Trim('"')
$AddonHome = Join-Path $env:USERPROFILE '.sca/addons'
$SkillsDir = Join-Path $env:USERPROFILE '.agents/skills'

& $ScaPython -m simplecadapi.cli --version
& $ScaPython -m simplecadapi.cli init --home $AddonHome --skills-dir $SkillsDir --no-shell
& $ScaPython -m simplecadapi.cli addon add --home $AddonHome --skills-dir $SkillsDir $Repo
```

仓库目录名、描述文件中的名称和技能名称须统一为 `sca-usd-export`。
当前 SCA 2.2.4 Windows 启动链要求安装代码与运行环境的根路径不含单引号或 `cmd` 元字符（如 `& | < > ^ % ! ( )`）；可以含空格。
如果用户目录不符合此条件，请为 `$AddonHome` 选择符合条件的位置；运行目录由 SCA 置于其相邻 `runtimes` 中。
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

& $ScaPython -m simplecadapi.cli addon use --home $AddonHome --skills-dir $SkillsDir sca-usd-export provision
& $ScaPython -m simplecadapi.cli addon check --home $AddonHome --skills-dir $SkillsDir sca-usd-export
```

只有当前检查成功后才进行导出。安装时显示的运行状态是缓存，不能代替重新检查。
准备环境需要获取 Python 依赖；导出本身不需要启动 Isaac Sim。

## 用包装器导出

SimpleCADAPI 2.2.4 在 Windows 的 `addon use` 中存在引用方式限制。
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

向上轴默认标为 `Y`。源模型约定 Z 向上时，在导出参数数组末尾加 `--up-axis Z`：

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
$ScaPython = (Read-Host '输入已安装 SCA 2.2.4 的宿主 python.exe 完整路径').Trim('"')
$BootstrapPython = (Read-Host '输入基础 Python 3.12 的 python.exe 完整路径').Trim('"')
$InputPackage = (Resolve-Path (Read-Host '输入已有 .scadpkg 的完整路径')).Path

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

## 输出能保留什么

- `.usda`：零件定义、组件实例、名称、层级、初始位姿，以及碰撞、质量、刚体分组和已支持关节的描述。
- `.report.json`：SCA 来源、计算结果、配置默认值、缺失信息和各级检查状态。
- `.history/`：原始定义 JSON、特征图 ZIP 及已捕获源码；USD 通过相对资源路径关联它们。
- 可选 `.html`：离线几何预览，不执行碰撞或动力学。

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
当前没有自动注册 `sca export --format usd`，也没有自动修复或改造输入模型。
几何建模请使用主 SimpleCADAPI 技能；产品包规范见该技能的 `references/scadpkg-format.md`，Schema 随 SCA wheel 位于 `simplecadapi/contracts/`。
