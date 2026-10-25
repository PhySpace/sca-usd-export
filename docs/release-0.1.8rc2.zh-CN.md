# 0.1.8rc2 本地候选与兼容边界

USD 0.1.8rc2 负责读取最终产品包、写出 USD/报告/历史并静态回读；physics 0.1.8rc1 负责物理声明、来源及持久化校验。两者无需同号。USD 的依赖与安装合同发生修正，沿用历史 rc1→rc2 规则；物理数据格式和求解策略未因本轮改动改变。

准确的实测版本、历史包哈希、旧参数来源迁移、升级顺序和短安装路径见[兼容与升级矩阵](compatibility-0.1.8rc2.zh-CN.md)。实际环境为 Windows x86-64、Python3.12.15、SCA SDK2.2.5、OpenUSD26.8、OCP7.9.3.1；仅此配对及所选输入通过。**旧无来源裸参数原样不兼容；按要求补充来源信息后，所测试案例通过。**可运行迁移例子来自配套physics的 `examples/pendulum/physics-input.json`，其来源明确是软件试验选择，不是实测数据。

## 依赖与环境

声明支持 Windows x86-64、CPython 3.12、SimpleCADAPI >=2.2.5,<2.2.6。必需运行依赖为 SimpleCADAPI、usd-core==26.8、numpy==2.3.1、cadquery-ocp==7.9.3.1、plotly==7.1.0；其传递依赖由 pip 校验。test extra 包含 pytest/jsonschema 和完整测试所需 sca-physics==0.1.8rc1；hatchling 属于构建依赖。Isaac Sim/Omni PhysX 只用于额外原生仿真，不是导出依赖，也不随包分发。

`sca-physics==0.1.8rc1` 属于 `physics` extra。没有 `metadata.sca_physics` 的几何输入不需要安装物理模块，仍沿用可追踪的旧默认物理描述。含物理 metadata 的包会直接调用 physics 的合同校验、预算或官方网格来源辅助函数；prepare-deformable 也调用其几何来源指纹辅助函数。两个插件在创作和导出进程间通过产品包持久化交换数据，但导出物理路径确实有 Python 模块依赖。

## 安装与最小复现

按 [README](../README.zh-CN.md) 使用 SCA 2.2.5 的 init、addon add、provision、check；数据路径使用 scripts/use.ps1 的 JSON/Base64 参数包装器。SCA 2.2.5 的本地 add 接受目录，本轮 ZIP 交付物先解压，再安装包含 sca-addon.toml 的插件根目录。不要把 ZIP 路径直接交给本机 addon add。

本轮深层结果目录的真实 provision 曾因 pip 解包 SCA 传递依赖 Jedi 的长类型桩路径触发 WinError206；本机未启用 Windows Long Paths。隔离 state/runtime 改用较短路径，日志继续归档到主 run；具体路径和两次结果保留在外部报告。不修改 SDK 或注册表。选择安装根时也须满足该环境限制，源代码能 import 不能替代完整依赖安装。

几何模式：清除 SCA_PHYSICS_SOURCE，在全新隔离位置安装 USD 并 provision/check，再以 `@('export', raw.scadpkg, new.usda)`、`@('validate', new.usda)` 检查。配套模式：将 SCA_PHYSICS_SOURCE 指向准确 physics 源/wheel 后 provision，先由物理公开 enrich/set-* 接口生成并独立重读最终包，再 export/validate；物理新流程推荐同时提供 expected-input-sha256 和 require-physics 守卫。完整命令及实测输入见本轮外部 REPRODUCE.zh-CN.md。

更新 payload 后必须重新 provision，版本号相同也不能替代文件校验。无 peer receipt 但发现遗留 physics 的环境拒绝加载；从配套环境回到真正独立几何模式应另选全新 runtime，不手删已安装模块。临时验收脚本可省略 --physics-source，前提是输入没有物理 metadata 且清除继承源变量。

check_cmd 按 SDK 文档约2秒目标实测完整启动链，不只测 Python 函数。本轮初测超过该目标，外部剖析后将成功退出改为正常 exit 0，非零仍精确保留原生错误码；任意参数的JSON/Base64校验不变。原型与最终安装包测量分开归档，以实际 SDK 复测为最终依据。

## 兼容与升级

现有 export_package、ExportConfig、CLI export/validate/history/preview、旧 --config 和 --up-axis 调用保留。物理 schema 读取范围仍为 1.0–1.8，但格式范围不等于已实测所有历史生成器；明确拒绝未知格式、错误单位、冲突覆盖和错配物理实现，不把失败当作无物理配置继续导出。历史 elasticity_damping 显式 unit:"1" 的不兼容修正继续生效，须确认原值语义后另存 unit:"1/s"，不自动换算。

“旧参数仍能调用”不代表历史缺省行为相同：本地保留的原仓 Git commit `9fa527b` 源码默认 up_axis 为 Y、default_restitution 为 0；当前无 metadata 输入默认 Z、0.1，并有明确的 default_dynamic_friction=0.4。另一个 2026-10-19 冻结的本地 0.1.0 副本已采用 Z/0.4/0.1；不能只凭版本号把两者视为同一代码。要保留 commit `9fa527b` 的轴标签与恢复系数，显式使用 `ExportConfig(up_axis="Y", default_restitution=0.0)`，并按任务明确动态摩擦等值；CLI 可使用 `--config` JSON。含保存物理 metadata 的输入以包内值为准，不能用导出 config 覆盖，须通过 physics 更新新包。严格来源的新无 schema 声明也不能默认接受旧裸布尔/数值，需迁移为有单位和 source 的 wrapper；旧 schema 已保存包按其版本合同读取。

纯几何用户只需升级 USD。已使用 physics 0.1.8rc1 的用户可保留物理版本，更新 USD 后重新 provision。较旧物理插件若需要本候选物理路径，先准备 0.1.8rc1 源/安装，再更新 USD 至 0.1.8rc2 并显式指定源 provision；不覆盖旧产品包或旧环境。不同物理实现版本不在此次配套范围。

## 验证记录与限制

本轮验证证据保存于 personal-workspace/results/addon-release-preparation/20261025_162555：REPORT.zh-CN.md、REPRODUCE.zh-CN.md、验证日志与安装包哈希。源码测试和安装包隔离验证分别记录，测试未结束前不以文档描述预写通过结论。实际通过的历史输入、版本和环境以该报告逐项记录为准；其他旧版本、其他操作系统/Python/SCA/OpenUSD 组合和 GitHub 下载安装未由本说明扩大支持。

静态导出始终保持 simulator_validated=false。本轮依赖修正未执行大规模 FEM、材料标定或视频任务；独立 OpenUSD 静态校验不能证明 PhysX 属性被执行，也不证明机械精度或所有 Isaac 物理属性支持。历史严格力学未通过项、Capsule 预览限制和原生 FEM 边界按原文保留。

两项外部CAD-FEM fixture检查、Isaac／PhysX行为、其他环境和全部历史版本组合明确列为未验证。所选schema1.0、1.1包和旧路径/内存API通过的范围不扩展到这些项目。此轮仅补充用户说明时保留代码版本，候选包由更新文档后的源码重新封装，使用新的包哈希；前轮原包和原始证据继续保留。
