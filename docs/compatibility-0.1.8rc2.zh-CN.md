# 0.1.8rc2 兼容、依赖与升级矩阵

这是rc预发布候选的兼容说明，不是稳定版保证。公开附件下载、校验、解压及官方SCA目录安装见[安装指南](release-installation.zh-CN.md)，实际下载验收结果见对应Release正文。导出端为 **sca-usd-export 0.1.8rc2**，物理写入/合同实现端为 **sca-physics 0.1.8rc1**；只验证这一准确配对，不为同号而调整版本。USD 负责最终包读取、USD/报告/历史及静态回读，physics 负责有来源的物理声明及持久化校验。

本地证据为工作区 `personal-workspace/results/addon-release-preparation/20261025_162555/` 的 REPORT、REPRODUCE、`compatibility/RESULTS.zh-CN.md`、`compatibility/latest-api-summary.json`、`pair-final/steps.json` 和 `standalone-final/steps.json`。本文件提取其用户边界；原输入、命令、逐进程模块路径和日志保留在该结果目录，未把运行环境或日志加入源码仓库。

## 环境与真实依赖

实际导出环境为 Windows x86-64、CPython **3.12.15**、SCA SDK **2.2.5**、OpenUSD/usd-core **26.8**（API `(0,26,8)`）、cadquery-ocp **7.9.3.1**、NumPy **2.3.1**、Plotly **7.1.0**。OpenUSD 来自隔离 venv 的 usd-core，不借用 Isaac 的 pxr。清单限 Windows x86-64、Python3.12、SDK>=2.2.5,<2.2.6；范围声明不替代具体版本实测。

| 类型 | 声明和用途 |
| --- | --- |
| 必需运行依赖 | `simplecadapi>=2.2.5,<2.2.6`、`usd-core==26.8`、`numpy==2.3.1`、`cadquery-ocp==7.9.3.1`、`plotly==7.1.0` |
| 可选物理运行依赖 | `physics` extra：`sca-physics==0.1.8rc1`；SCA provision 由显式 `SCA_PHYSICS_SOURCE` 选取准确源目录或 wheel 并记录身份 |
| 开发测试依赖 | `test` extra：`pytest>=8,<10`、`jsonschema>=4,<5` 及完整物理测试所需 `sca-physics==0.1.8rc1`；不是普通几何运行必需项 |
| 构建依赖 | build-system 的 `hatchling>=1.27,<2`；不将构建器当成用户导出 API |
| 仿真环境 | Isaac Sim／Omni PhysX 由调用者另备；不参与独立几何导出，不随包分发，本次未运行 |

插件进程通过产品包根定义 `metadata.sca_physics` 交换保存的数据，USD 的物理路径还直接导入 `sca_physics.contract`、链预算和官方网格来源辅助函数，`prepare-deformable` 也调用其几何来源指纹辅助函数。因此物理路径确实有可选 Python 模块依赖。无此 metadata 的 raw 包走 legacy 几何路径，隔离环境确认物理模块及 distribution 都不存在后仍可导出、静态重开；该路径保留已有可追踪默认物理描述，未新增“去掉全部物理 prim”功能。

## 所测旧包与 API

下列路径相对于工作区 `personal-workspace/`。保留原输入，另写读取/编码重载及导出结果；生成器版本由当次清单或运行日志确认，不按文件后缀推测。

| 输入/调用 | 结果与限制 |
| --- | --- |
| `results/sca-physics/20261019_stable_coexistence_184500/validation/final.scadpkg` | physics0.1.0、物理schema1.0；SDK读取/原生加载/编码重载、完整物理快照及USD导出/重开通过；SHA256 `ba39ea3d58fbe8c59ca00153858fd72d6d00f091f71602374461ec6c7f4406ec` |
| `results/sca-physics/20261020_authoring_mvp_0_1_3rc1/legacy-flow/attempt-02/mechanical.scadpkg` | physics0.1.3rc1、物理schema1.1；同上通过；SHA256 `54f37bbfd0576d88a2d7f7a718718fce0fa29cf8e3540601b3e1fcc29caf8893` |
| 路径 API | `export_package(path, output, config=ExportConfig.from_json(json_path))` 通过，采用冻结本机0.1.0的全部配置字段和值 |
| 内存 API | `export_package(ProductPackage, output, config=ExportConfig(...))` 同样通过；新增守卫参数保持可选 |
| CLI | `template/enrich/validate --expect`→`export/validate/history/preview`、旧 `--config` 和显式 `--up-axis`、刚体链声明流程通过 |
| 当前物理数据 | 所测schema1.7/1.8经公开写入、保存、独立SDK重载再导出，核对false/0、刚体/接触参数和普通关节驱动/articulation实际authored属性 |
| 旧裸参数 | 原样拒绝；下述来源迁移后，所测schema1.0完整流程通过 |

接受物理schema1.0–1.8不等于全部历史包实测。更早GitHub提交 `9fa527b` 的缺省为Y向上、恢复系数0；当前无metadata缺省为Z、0.1，冻结本机0.1.0已是后者。要维持更早选择，显式给出 `ExportConfig(up_axis="Y", default_restitution=0.0)` 或对应JSON，按任务核实摩擦等值。含物理metadata时这些物理config覆盖会拒绝，应通过physics另存新包。

## 旧来源参数迁移

**旧无来源裸参数原样不兼容；按要求补充来源信息后，所测试案例通过。**实际旧示例中 `joints.joint/passive_pendulum/hinge.collision_enabled=false` 被报为 `.source: required`。迁移只给原值增加正确单位和真实来源，保持false、其他值及schema1.0，不虚构出处：

```json
{"value": false, "unit": "boolean", "source": {
  "kind": "estimated",
  "detail": "Software fixture deliberately disables collision between the hinge bodies; not a measured property"
}}
```

这是随包软件试验选择的真实描述，不能复制后称为真实设备测量。用户明确输入用 `explicit` 并写清要求；工程估算用 `estimated` 并注明假设/未标定范围；资料用 `reference` 并附真实 `citation`、适用 `conditions`；推导需保存实际输入及固定公式。每值至少有正确 `value/unit/source.kind/detail`，或按合同使用真实声明/根来源继承；缺来源不能只批量标成用户输入。确认数据真实意义后再选择来源类型。

可运行示例为准确配套physics候选的 `examples/pendulum/physics-input.json`，其中上述字段已经修正；其输入为同包 `verification/raw.scadpkg`。安装、provision和check后，用两个已安装payload的包装器运行，下例中的变量由用户选定，输出目录必须是新目录：

```powershell
# $PhysicsPayload / $UsdPayload 是各自已安装的payload根；$Common含
# ScaPython、AddonHome、SkillsDir，按各仓README设置，不使用skill副本作代码。
$Raw = Join-Path $PhysicsPayload 'verification/raw.scadpkg'
$Config = Join-Path $PhysicsPayload 'examples/pendulum/physics-input.json'
$Final = Join-Path $NewOutput 'final.scadpkg'
$Usd = Join-Path $NewOutput 'final.usda'
& "$PhysicsPayload/scripts/use.ps1" @Common -Arguments @('enrich', $Raw, '--config', $Config, '--output', $Final)
if ($LASTEXITCODE -ne 0) { throw 'enrich failed' }
& "$PhysicsPayload/scripts/use.ps1" @Common -Arguments @('validate', $Final)
if ($LASTEXITCODE -ne 0) { throw 'physics validation failed' }
$Digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $Final).Hash.ToLowerInvariant()
& "$UsdPayload/scripts/use.ps1" @Common -Arguments @('export', $Final, $Usd, '--require-physics', '--expected-input-sha256', $Digest)
if ($LASTEXITCODE -ne 0) { throw 'export failed' }
& "$UsdPayload/scripts/use.ps1" @Common -Arguments @('validate', $Usd)
if ($LASTEXITCODE -ne 0) { throw 'USD validation failed' }
```

已有 `elasticity_damping` 显式 `unit:"1"` 的旧FEM声明也不兼容；先确认数值含义，再在新配置改为 `1/s`，不自动换算数值或改写原包。自动线缆输入必须显式给正确category，不能依赖description兜底。新无schema声明默认1.7，含新增关节字段时1.8；保留旧调用形式不代表所有缺省写入行为不变。

## 升级和错配提示

| 用户 | 是否需同时升级 | 推荐操作 |
| --- | --- | --- |
| 输入没有物理metadata，且不使用prepare-deformable | 否，只需USD | 清除继承的 `SCA_PHYSICS_SOURCE`，用全新几何home/runtime安装USD0.1.8rc2并provision/check |
| 已有准确physics0.1.8rc1 | 不必重复升级physics | 更新USD至0.1.8rc2，显式选择该physics源/wheel并重新provision/check |
| 使用其他physics版本，需要此候选物理路径 | 需要准备准确配套 | 先在新home准备physics0.1.8rc1，再安装/更新USD0.1.8rc2并指定源provision/check，最后切换写入/导出调用；保留旧包和旧环境 |

只升级写入端不能证明旧导出器认识新1.8字段；旧导出器读取新包未实测。每次更新payload都重新provision，不能只看版本号。含物理metadata而缺模块时提示 `requires optional sca-physics==0.1.8rc1`；错模块版本提示 `Physics implementation mismatch`；provision选择错产物提示 `Physics artifact does not match this USD release` 并返回2。无peer receipt但发现遗留模块提示 `Unreceipted optional sca-physics`，不得把这种环境称为独立几何验证。未知schema9.9和已测错配明确拒绝、无USD残留，没有静默丢弃物理属性后报告成功。

## 安装路径与未验证范围

SCA2.2.5本地add接受目录：候选ZIP先解压，再 `addon add <含sca-addon.toml的目录>`，不把ZIP直接作为add输入。短状态根实测为工作区下 `personal-workspace/results/arp-20261025-162555/s`（独立）和 `p`（配套），home为`s/a`、`p/a`，skills为`s/s`、`p/s`，runtime在`s/runtimes`、`p/runtimes`。新安装选新的同等短根；这些是本地已测路径建议，不是硬编码必需路径。深路径实测pip解包Jedi依赖失败（WinError206），不能仅import通过便称安装成功。安装代码/runtime根不能含单引号或cmd元字符；数据路径可以含中文、空格、单引号和&，但必须经包装器JSON/Base64传输。具体命令及隔离用户配置要求见[README](../README.zh-CN.md)。

**未验证：两项外部CAD-FEM fixture检查、Isaac／PhysX行为、其他系统/Python/SDK/OpenUSD组合、全部历史版本组合和全部旧参数分支。**物理schema1.2–1.6的全部真实历史成品没有逐一实跑；前轮证据未包含GitHub下载安装；本次附件下载验收实际结果以对应Release正文为准。add-on安装、保存/重载、静态USD成功不证明原生物理行为、工程精度或支持所有Isaac属性。输出保持 `simulator_validated=false`，既有严格力学失败与Capsule/预览/FEM边界继续保留。
