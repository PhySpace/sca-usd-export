# 从 GitHub Pre-release 安装 sca-usd-export 0.1.8rc2

源码、文档、技能资源和必要测试在仓库 `main`；安装包由 [v0.1.8rc2 Pre-release](https://github.com/PhySpace/sca-usd-export/releases/tag/v0.1.8rc2) 的附件分发。下载 **`sca-usd-export-0.1.8rc2.zip`** 和同页 **`SHA256SUMS.txt`**；GitHub 自动生成的 **Source code (zip/tar.gz)** 是源码快照，不是本指南使用的插件安装包。

这是 rc 候选，非稳定版，不标记 Latest。实际验证配对仅为 **physics 0.1.8rc1 + USD 0.1.8rc2**；配套 [physics Pre-release](https://github.com/wwlloo/sca-physics/releases/tag/v0.1.8rc1)。环境为 Windows x86-64、Python3.12.15、SCA SDK2.2.5、OpenUSD26.8、OCP7.9.3.1（NumPy2.3.1、Plotly7.1.0）；其他组合不作保证。GitHub 附件下载及安装的本轮实际结果见对应 Release 正文，不用本地包测试代替下载验收。

USD读取最终产品包并生成USD、报告和历史，支持静态回读。必需运行依赖是SDK同范围、usd-core26.8、NumPy2.3.1、cadquery-ocp7.9.3.1、Plotly7.1.0；physics extra为可选且精确的 `sca-physics==0.1.8rc1`，物理路径确实直接调用其Python合同/几何模块。无嵌入物理配置、不使用prepare-deformable时无需physics；并不新增“去掉全部物理prim”的能力。hatchling是构建依赖，test extra是开发验证，Isaac/PhysX是外部仿真环境。

## 下载、核验和解压

先在新的 PowerShell 窗口执行以下设置。示例短根 `C:/sca-rc` 可替换为你有写权限的全新短目录；目录必须尚不存在，不能指向已有安装。安装代码和 runtime 根不能含单引号或 cmd 元字符（`& | < > ^ % ! ( )`）。Windows 未启用长路径时，深目录曾使 Jedi 依赖解包报 WinError206；没有通用最大长度保证。

宿主 Python 必须已合法安装 SCA SDK 2.2.5。独立 runtime 仍需取得其依赖：提供可访问的包索引，或包含 SDK 2.2.5、传递依赖和 hatchling 的可信 wheelhouse。插件 ZIP 不附带 SDK、OCP、OpenUSD 或 Isaac 二进制；仅宿主已装 SDK 不等于 pip 能在新 runtime 取得它。离线安装须自行备齐全部匹配 wheel，不能用跳过依赖来伪装成功。既有本地验证使用明确的离线wheelhouse；在线索引完整性未验证，本次下载包的环境以Release正文为准。

```powershell
$ErrorActionPreference = 'Stop'
$ScaPython = (Read-Host '已安装 SCA 2.2.5 的 python.exe 完整路径').Trim('"')
$Python312 = (Read-Host '基础 Python 3.12.15 的 python.exe 完整路径').Trim('"')
$Root = [IO.Path]::GetFullPath((Read-Host '全新短目录，例如 C:/sca-rc').Trim('"'))
if (Test-Path -LiteralPath $Root) { throw '目录已存在，请选择全新短目录' }
New-Item -ItemType Directory -Path $Root -ErrorAction Stop | Out-Null

# 当前新窗口专用；重定向整条命令链，而非仅 init 一条命令。
# 自定义 home/skills-dir 本身不能阻止 SDK 写真实用户 ~/.sca/config.toml。
Get-ChildItem Env: | Where-Object { $_.Name -like 'SCA_*' } | Remove-Item
$ProfileDir = Join-Path $Root 'u'
New-Item -ItemType Directory -Path $ProfileDir -ErrorAction Stop | Out-Null
$env:USERPROFILE = $ProfileDir
$env:HOME = $ProfileDir
$env:APPDATA = Join-Path $ProfileDir 'AppData/Roaming'
$env:LOCALAPPDATA = Join-Path $ProfileDir 'AppData/Local'
New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA | Out-Null
& $ScaPython -I -B -c 'import sys; from pathlib import Path; assert Path.home().resolve() == Path(sys.argv[1]).resolve(); print(Path.home())' $ProfileDir
if ($LASTEXITCODE) { throw '专用用户配置目录校验失败' }
$AddonHome = Join-Path $Root 'a'
$SkillsDir = Join-Path $Root 's'
$env:SCA_PHYSICS_BOOTSTRAP_PYTHON = $Python312
$env:SCA_USD_BOOTSTRAP_PYTHON = $Python312
# 可选：只能填写自己合法取得的依赖目录；不固定个人机器路径。
# $DependencyWheels = (Resolve-Path (Read-Host '可信 wheelhouse 完整路径')).Path
# $env:SCA_ADDON_WHEELHOUSE = $DependencyWheels
# 完全离线且依赖齐全时再设置 $env:PIP_NO_INDEX = '1'。

function Get-AddonRelease([string]$Repository, [string]$Tag, [string]$Name, [string]$Version) {
    $Folder = Join-Path $Root "download/$Name"
    New-Item -ItemType Directory -Path $Folder -ErrorAction Stop | Out-Null
    $Filename = "$Name-$Version.zip"
    $Zip = Join-Path $Folder $Filename
    $Sums = Join-Path $Folder 'SHA256SUMS.txt'
    $Url = "https://github.com/$Repository/releases/download/$Tag"
    Invoke-WebRequest -Uri "$Url/$Filename" -OutFile $Zip
    Invoke-WebRequest -Uri "$Url/SHA256SUMS.txt" -OutFile $Sums
    $Pattern = '^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($Filename) + '$'
    $Entries = @(Get-Content -LiteralPath $Sums -Encoding UTF8 | Where-Object { $_ -match $Pattern })
    if ($Entries.Count -ne 1) { throw '校验文件没有唯一、准确的 ZIP 文件名记录' }
    $Expected = ([regex]::Match($Entries[0], $Pattern)).Groups[1].Value
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash
    if ($Actual -ne $Expected) { throw "SHA256 不匹配：$Filename" }
    Write-Host "$Filename SHA256 $Actual"
    $Extracted = Join-Path $Root 'src'
    Expand-Archive -LiteralPath $Zip -DestinationPath $Extracted -ErrorAction Stop
    $Source = Join-Path $Extracted $Name
    if (-not (Test-Path -LiteralPath (Join-Path $Source 'sca-addon.toml'))) { throw '解压布局缺少插件根清单' }
    return $Source
}

function Assert-AddonReady([string]$Name) {
    & $ScaPython -I -B -m simplecadapi.cli addon check --home $AddonHome --skills-dir $SkillsDir $Name
    if ($LASTEXITCODE) { throw "$Name check 命令失败" }
    $Registry = Get-Content -LiteralPath (Join-Path $AddonHome 'registry.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Registry.addons.$Name.runtime_check.passed -ne $true) { throw "$Name runtime_check 未通过" }
}
```

这些命令写入所选专用 profile、home、skill 和相邻 runtime，不修改已存在的稳定入口。完成后退出该 PowerShell 窗口；不要将专用 profile 变量写入全局环境。普通用户若要注册到自己的现用配置，应另行明确选择其安装位置；本指南默认使用独立新位置。

## 选择纯几何或配套物理流程并安装

同一 PowerShell 窗口继续执行。`$WithPhysics = $false` 使用全新的无physics runtime；已有配套runtime不能仅清除源变量后称为独立几何环境。SDK2.2.5 接受**解压后含 `sca-addon.toml` 的目录**，不是 ZIP 路径；目录名保持插件名。

```powershell
$WithPhysics = $false   # 需要包内物理配置或prepare-deformable时改为 $true
$UsdSource = Get-AddonRelease 'PhySpace/sca-usd-export' 'v0.1.8rc2' 'sca-usd-export' '0.1.8rc2'
& $ScaPython -I -B -m simplecadapi.cli init --home $AddonHome --skills-dir $SkillsDir --no-shell
if ($LASTEXITCODE) { throw 'SCA init 失败' }
if ($WithPhysics) {
    $PhysicsSource = Get-AddonRelease 'wwlloo/sca-physics' 'v0.1.8rc1' 'sca-physics' '0.1.8rc1'
    & $ScaPython -I -B -m simplecadapi.cli addon add --home $AddonHome --skills-dir $SkillsDir $PhysicsSource
    if ($LASTEXITCODE) { throw 'physics add 失败' }
    & $ScaPython -I -B -m simplecadapi.cli addon use --home $AddonHome --skills-dir $SkillsDir sca-physics provision
    if ($LASTEXITCODE) { throw 'physics provision 失败' }
    Assert-AddonReady 'sca-physics'
    $env:SCA_PHYSICS_SOURCE = $PhysicsSource
} else {
    Remove-Item Env:SCA_PHYSICS_SOURCE -ErrorAction SilentlyContinue
}
& $ScaPython -I -B -m simplecadapi.cli addon add --home $AddonHome --skills-dir $SkillsDir $UsdSource
if ($LASTEXITCODE) { throw 'USD add 失败' }
& $ScaPython -I -B -m simplecadapi.cli addon use --home $AddonHome --skills-dir $SkillsDir sca-usd-export provision
if ($LASTEXITCODE) { throw 'USD provision 失败' }
Assert-AddonReady 'sca-usd-export'
$UsdPayload = Join-Path $AddonHome 'sca-usd-export'
$UsdPython = Join-Path $Root 'runtimes/sca-usd-export/.venv/Scripts/python.exe'
& $UsdPython -I -B -c "import importlib.util,sca_usd_export; print(sca_usd_export.__file__); print(importlib.util.find_spec('sca_physics'))"
if ($LASTEXITCODE) { throw 'USD 模块加载失败' }
```

独立模式最后应打印physics为`None`；USD应来自本次runtime的site-packages。配套模式两插件会各有私有runtime，USD自己的runtime也会安装准确physics peer。不要把全局已安装副本、skill目录或PYTHONPATH注入当作验证。管理命令返回0不能单独证明probe通过，以上检查了registry真实`runtime_check.passed`。

## 最小使用

纯几何模式输入应是已完成、没有嵌入物理配置的真实 `.scadpkg`，不是改扩展名的网格：

```powershell
$Out = Join-Path $Root 'out'
New-Item -ItemType Directory -Path $Out -ErrorAction Stop | Out-Null
$Raw = (Resolve-Path (Read-Host '已有无物理配置 .scadpkg 完整路径')).Path
$Usd = Join-Path $Out 'geometry.usda'
& $UsdPython -I -B -m sca_usd_export export $Raw $Usd
if ($LASTEXITCODE) { throw '几何导出失败' }
& $UsdPython -I -B -m sca_usd_export validate $Usd
if ($LASTEXITCODE) { throw 'USD 重新读取失败' }
```

若上一步选择 `$WithPhysics = $true`，可改运行下面的随包来源迁移例子。以下 `$Out`也必须是全新目录；不要在已经创建它后重复New-Item。

```powershell
$Out = Join-Path $Root 'out'
New-Item -ItemType Directory -Path $Out -ErrorAction Stop | Out-Null
$PhysicsPayload = Join-Path $AddonHome 'sca-physics'
$Raw = Join-Path $PhysicsPayload 'verification/raw.scadpkg'
$Config = Join-Path $PhysicsPayload 'examples/pendulum/physics-input.json'
$Final = Join-Path $Out 'final.scadpkg'
$Usd = Join-Path $Out 'final.usda'
# 读取端与依赖已通过check，才开始使用匹配物理写入端。
& $UsdPython -I -B -m sca_physics enrich $Raw --config $Config --output $Final
if ($LASTEXITCODE) { throw '附加物理配置失败' }
& $UsdPython -I -B -m sca_physics validate $Final
if ($LASTEXITCODE) { throw '物理配置独立重载失败' }
$Digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $Final).Hash.ToLowerInvariant()
& $UsdPython -I -B -m sca_usd_export export $Final $Usd --require-physics --expected-input-sha256 $Digest
if ($LASTEXITCODE) { throw '配套物理导出失败' }
& $UsdPython -I -B -m sca_usd_export validate $Usd
if ($LASTEXITCODE) { throw 'USD 重新读取失败' }
```

这些直接Python调用运行在准确的私有venv；需要经SCA统一入口安全传递中文、空格或shell字符路径时，使用已安装payload的`scripts/use.ps1`字符串数组包装器，详见[README](../README.zh-CN.md#用包装器导出)。不要拼接原始数据到`addon use`命令字符串。

## 兼容、升级和限制

**旧无来源裸参数原样不兼容；按要求补充来源信息后，所测试案例通过。**上面随包pendulum例子保留`collision_enabled=false`和schema1.0，增加真实软件fixture估计来源；不能把它当作实测设备依据。真正迁移时保留值/单位，补真实`source.kind/detail`，资料引用还需真实citation/conditions。来源可在字段、组或根继承，不能为了过校验虚构出处。

所测旧包仅为2026-10-19 physics0.1.0/schema1.0摆臂包与2026-10-20 physics0.1.3rc1/schema1.1机械包；通过读取/SDK原生重载/完整物理快照与USD导出。旧路径/内存`export_package`及所测JSON ExportConfig有效；更早GitHub缺省Y/恢复0与当前Z/0.1不同，需显式配置。确切输入hash、旧参数单位/category限制及错配错误见[兼容矩阵](compatibility-0.1.8rc2.zh-CN.md)。

无物理metadata用户只升级USD即可；已有准确physics0.1.8rc1者无需重复升级。其他物理用户先准备并检查USD0.1.8rc2及其准确peer，再切换physics写入入口；只升级写入端不能证明旧导出器识别schema1.8。既有安装使用官方`addon update`后重新provision/check，版本相同也须检查文件身份。保留旧环境/原包，不默认remove，不手改site-packages或全局skill。缺peer、错版本或未知schema会明确失败，不静默丢弃物理属性。

**未验证：两项外部CAD-FEM检查、Isaac／PhysX行为、其他环境、全部历史版本组合及全部旧参数分支。**现有Capsule预览、FEM、自然态和力学精度限制保留。安装、保存、重载及静态USD成功不证明原生仿真或所有Isaac物理属性通过；输出保持`simulator_validated=false`。
