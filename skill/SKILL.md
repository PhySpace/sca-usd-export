---
name: sca-usd-export
description: 用户要求把 SimpleCADAPI 已生成的 .scadpkg 导出到 OpenUSD，或检查导出的 USD、物理描述、建模历史和预览时使用。读取产品包的定义、实例图、已计算 BREP、连接器、约束及特征图，输出 .usda、报告、历史资源和可选 HTML。几何修改先回到主 SimpleCADAPI 建模流程并重新 capture。
---

# SimpleCADAPI 到 OpenUSD

本 add-on 名为 `sca-usd-export`，版本 `0.1.0`，当前声明支持 Windows x86-64、Python 3.12、SimpleCADAPI `>=2.2.4,<2.2.5`。
它消费完成的产品包；不注册 `sca export --format usd`，不改变主 SCA 命令或建模环境。

## 运行前检查

1. 使用用户选定的宿主 SCA 命令运行 `sca addon check sca-usd-export`；自定义安装位置时同时提供对应 `--home`、`--skills-dir`。
2. 检查失败时报告缺失的运行环境并停止导出。运行环境准备入口是 `sca addon use sca-usd-export provision`；它在独立 `runtime_dir/.venv` 中安装依赖，不继承宿主建模环境的包。
3. 可以用 `sca addon use sca-usd-export` 查看实际 `addon_dir` 和 `runtime_dir`。代码属于安装目录，虚拟环境和缓存属于运行目录，导出文件由调用者选择。
4. 运行环境准备后再次执行检查，不用安装时缓存的检查结果代替当前检查。

宿主 SCA Python 仅运行 SCA CLI；USD、SCA 读取器、OCP、NumPy 等实际导出依赖安装在 add-on 自己的环境。
`pyproject.toml` 固定 `cadquery-ocp==7.9.3.1`，与原实现已测内核版本一致；准备环境可能下载较大的二进制依赖包。
如需指定用于创建环境的基础 Python 3.12，设置 `SCA_USD_BOOTSTRAP_PYTHON` 为其可执行文件路径。
未指定时准备脚本尝试 `py -3.12` 和基础 `python`，并校验版本。

## 安全传参和调用

SimpleCADAPI 2.2.4 在 Windows 的 `addon use` 实现将 POSIX 风格引用交给 `cmd`。
带空格、中文或 shell 字符的文件路径不要直接拼入其数据命令字符串。
使用已安装目录的 `scripts/use.ps1`，传入宿主 `-ScaPython`、安装位置 `-AddonHome/-SkillsDir`，以及真正的字符串数组 `-Arguments`。
包装器将参数数组编码为 UTF-8 JSON 的 Base64，经 `argv-json-base64` 入口传入独立 Python；不把数据解释为代码。
安装代码和运行环境的根路径不能含单引号或 `cmd` 元字符（如 `& | < > ^ % ! ( )`）；空格可用。这是 SCA 2.2.4 Windows 启动链的限制。
模型输入、输出和配置路径不受此限制，可以含空格、中文、单引号和 `&`，但必须通过包装器编码传递。
PowerShell 传输层限 1～256 个参数，Base64 token 最长 6000 字符；Python 内部虽允许 64 KiB，外层 `cmd` 容量更小，仍须遵守传输层限制。
超长配置写入 JSON 并传 `--config` 路径；不能拆分 token 或退回拼接原始 shell 数据。

支持的参数数组形式：

- `@('export', inputPackage, newUsdaPath)`；可附加 `--config` 和 JSON 路径、`--up-axis Y` 或 `--up-axis Z`。
- `@('validate', usdaPath)`：重开 USD，并读取相邻报告与历史。
- `@('history', historyManifest)`：读取历史清单；附加 `--definition` 和定义 ID 可读取特征图。
- `@('preview', usdaPath, newHtmlPath)`：创建离线几何预览。
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

源定义长度单位为毫米，顶点和局部平移各转一次米；USD 质量单位为千克。
默认 `up_axis=Y`，可用 `--up-axis Z` 改变标签和重力方向；这不会旋转、居中或重新定向原几何。
必须核对输入模型自己的坐标约定，不能把演示模型的默认向上轴当作 SCA 的全局保证。

## 结果解释

- 保留定义、实例 ID、名称、层级及已求解初始位置；共享定义通过 USD 内部引用复用。
- 显式固定关系决定刚体分组；独立部件可以属于同一刚体。旋转和滑动关系使用真实连接器轴、锚点和限位。
- 密度优先取 SCA 材料；缺失密度、摩擦和碰撞近似等配置必须报告为配置或默认值，不能称作测量数据。
- 历史目录保留原始定义、特征图、操作参数、依赖及已捕获源码。读取历史不会执行建模；USD 不能因此执行拉伸、切除或倒角。
- 完整 CAD 恢复或装配重放仍需原始 `.scadpkg`；历史目录不复制 BREP、材料和拓扑缓存。
- 参数变更、重建几何及修复建模问题回到主 SCA 流程，重新求解和捕获，再导出。

分别报告写出成功、几何与静态物理描述检查、实际仿真器验证。
没有运行 Isaac Sim 时，始终保持 `simulator_validated=false`，不承诺碰撞生成、机器人接触或推门结果。
现有铰接示例仍有未解决的错位问题；不得把它作为运动或装配正确的验收样例，也不得自行调整几何掩盖问题。
GitHub 安装流程目前未验收；已声明的平台和版本范围不代表其他组合也受支持。
