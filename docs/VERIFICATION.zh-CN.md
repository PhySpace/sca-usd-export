# 0.1.0 本地验收记录

本记录对应 2026-10-16 的实际 Windows 验收。已通过本地 SCA add-on 安装、独立环境准备、运行检查、经 `addon use` 导出及 OpenUSD 重新读取；没有执行 GitHub 安装和 Isaac Sim 仿真。

## 规范与改造依据

- 原项目 SimpleCADAPI 版本：2.2.4；提交 `750dad08e26b2d342df872c2306cb54f07cb2c4c`，标签 `v2.2.4`。
- 已读取原项目 `AGENT.md`、`docs/skill/SKILL.md`、`docs/skill/references/domains/addon-development.md`，以及 add-on 描述解析、安装、检查和 use 实现。
- 描述文件只使用当前规范的 `[addon]`、`[compat]`、`[runtime]`。名称、预期仓库名和技能名均为 `sca-usd-export`，符合当前 slug 规则。
- 只声明 Windows x86-64、Python 3.12、SCA `>=2.2.4,<2.2.5`。Apache-2.0 依据原项目许可；复用来源见根目录 `NOTICE`。
- 现有导出器迁移到 `src/sca_usd_export`。几何、物理、历史和验证算法沿用原实现；新增内容集中于安装、依赖、运行检查和 Windows 参数传输。
- SCA 当前不会替插件安装 Python 环境；本插件通过 `addon use ... provision` 显式准备 `runtime_dir/.venv`。

## 实际环境与隔离

| 项目 | 实际版本或结果 |
| --- | --- |
| Windows / Python | Windows x64 / CPython 3.12.15 |
| SimpleCADAPI | 2.2.4，重新安装在插件环境内 |
| OpenUSD | usd-core 26.8 |
| 几何与预览依赖 | cadquery-ocp 7.9.3.1、numpy 2.3.1、plotly 7.1.0 |
| 插件 / 测试 | sca-usd-export 0.1.0 / pytest 9.1.1 |
| 解释器隔离 | `-I -B`；`include-system-site-packages = false` |
| 实际模块来源 | SCA、pxr、numpy、OCP、plotly、插件均位于新的 `runtime_dir/.venv/Lib/site-packages` |
| 依赖一致性 | `pip check` 退出 0，`No broken requirements found.` |

原建模环境的 Python 仅用于启动 SCA 安装管理和环境准备命令。准备脚本使用基础 Python 创建全新 venv，没有复制原环境的 site-packages。准备完成后，SCA 的 check/use/list 管理命令也切换到插件环境的 Python。最终导出与验证均通过已安装的插件运行，没有从原项目源码或原 `.venv` 导入 SCA。

验收把子进程 `USERPROFILE` 指向新的临时目录，并在执行 `init` 前断言 `Path.home()` 已重定向。SCA 配置、技能、代码、运行环境、输入副本和输出均留在临时验收目录；没有改变真实用户的 SCA 配置和 PATH。

下载二进制依赖时曾遇到网络超时，并保留了失败记录。最终使用可选 `SCA_USD_WHEELHOUSE` 输入 9 个公开 PyPI wheel：完整文件共 250,921,234 字节，每个 SHA-256 均与 PyPI 元数据一致，未关闭 TLS 验证。其余依赖由 pip 解析安装。没有从旧环境借用已安装包。wheel 清单和校验值记录在验收目录的 `download-manifest.json`。

## 实际命令与结果

下表省略重复的临时 `--home`、`--skills-dir` 参数；所有完整命令、工作目录、环境变量、退出码和日志路径都保存在 `acceptance.json`。PowerShell 数据命令使用已安装的 `scripts/use.ps1`，它内部调用真实的 `sca addon use`，并通过 Base64 JSON 传递参数。

| 实际检查 | 结果 |
| --- | --- |
| SCA `init --no-shell`、`addon add <本地插件目录>` | 退出 0；初次运行环境尚不存在的检查警告属实保留 |
| 修正安装细节后的 `addon update sca-usd-export` | 退出 0，使用同一临时配置，运行目录保留 |
| `addon use sca-usd-export provision` | 最终退出 0，新环境安装与原生依赖导入成功 |
| `addon check sca-usd-export` | 退出 0，并核实登记记录的 `runtime_check.passed = true` |
| `use.ps1 -Arguments @('check')` | 退出 0，输出独立解释器、依赖和模块来源 |
| `use.ps1 -Arguments @('export', <输入>, <新输出>)` | 退出 0，实际经 SCA `addon use` 导出 |
| `use.ps1 -Arguments @('validate', <输出>)` | 退出 0，OpenUSD 重新读取后验证通过 |
| `history`、`preview` | 均退出 0；历史可读，离线 HTML 生成成功 |
| 重复导出到已有输出 | 退出 2，拒绝覆盖，错误码贯穿启动链 |
| 输入文件不存在 | 退出 2，明确报告缺失 |
| `addon list` | 退出 0，运行检查状态成功 |
| 中文文件名额外导出与重开验证 | 均退出 0，结果见 `unicode-validation.json` |
| `python -I -B -m pytest <插件目录>/tests -q -p no:cacheprovider --basetemp <验收目录>/pytest-temp` | 退出 0，**78 passed in 7.17s** |

验收从与源码无关的工作目录运行，并对输入输出使用相对路径。安装目录、技能目录、运行目录和工作目录均包含空格。实际数据文件名包括 `CAD O'Brien & bracket.scadpkg`、`USD O'Brien & bracket.usda`，额外覆盖中文、空格、单引号和 `&` 同时出现的路径。

安装根路径仍需遵循 README 中的字符限制；数据路径的编码处理不代表任意安装根路径都已支持。

## 真实模型结果

输入是原项目 `out/u_link_motor_mount.scadpkg` 的逐字节副本，SHA-256 为 `a1663bd0f5e08e2840c47456e8615dc34e1c208c0a30150bc4de033700e6eaa9`。直接读取产品包中的定义、实例和 BREP，逐零件三角化；没有生成或读取中间 STL。

| 验证项 | 实际结果 |
| --- | --- |
| 默认模型 / 单位 / 向上轴 | `/Model` / 1 米每单位 / Y |
| 结构 | 3 个语义实例，其中 2 个零件实例 |
| 网格 | 2 个语义网格和 2 个物理网格；语义表示隐藏，物理表示可见 |
| 三角形 | 零件总计 17,246；包含两套表示的组合总计 34,492 |
| 世界尺寸 | 约 110 × 19.5 × 30 毫米 |
| 最大变换误差 / 最大包围盒误差 | 0 / 约 1.04e-9 米 |
| 物理静态检查 | 通过；2 个碰撞形状，0 个动态刚体，0 个关节 |
| 历史 | 3 个定义的历史资源可读取 |

本示例不包含可动关节，不能用于证明摆动或推门。单位、向上轴、密度等配置沿用迁移前实现，来源与默认值仍由每次导出的 `.report.json` 逐项记录。本轮没有扩展物理功能。

## 本机证据位置与原文件保护

本次保留的验收根目录：

```text
C:\Users\16218\AppData\Local\Temp\sca usd addon acceptance 9t250mk9
```

主要文件：

- `acceptance.json`：包括早期失败和最终成功的完整命令、退出码、日志路径；最终 `ok = true`。
- `runtime-facts.json`：实际解释器、依赖版本和各模块来源。
- `pytest.json`、`pytest.log`：测试命令、78 项结果、版本和 pip 检查。
- `unicode-validation.json`：中文及特殊字符路径的额外实测。
- `preservation.json`：原项目 Git 状态、原目录内容指纹及本轮前后比较。
- `unrelated working directory/output models/`：USD、报告、历史目录和 HTML 预览。

原项目 Git 开始和结束状态均为 `master...origin/master`，只有原已存在的未跟踪 `extensions/`、`out/`，`git diff HEAD --name-status` 为空。对 `extensions/` 的 27 个文件与 `out/` 的 59 个文件重新计算包含相对路径及文件内容 SHA-256 的目录指纹，与开始时一致：

```text
extensions fbd9043ec0e5bfeb9a73a4e2d94e31764be5a394fe7204b619e440864881511e
out        d62b0d2ffebc112dc804b8c5df74d43659621e9c7550db45afedb80fc2e446cc
```

独立插件没有创建 Git 仓库，没有提交和上传。后续应将本插件目录内容上传到名为 `sca-usd-export` 的仓库；不要上传运行环境、下载缓存或本机临时验收目录。

## 尚未验证与已知限制

尚未从公开 GitHub 仓库安装，没有运行 Isaac Sim，没有解决此前铰接摆臂播放后错位的问题。此处的物理检查是 USD 数据的静态检查，不证明 PhysX 已加载关节或发生正确运动。其他平台与 Python/SCA 版本也没有声明支持。

当前版本具备交给学长在上述 Windows 范围内进行安装与导出测试的条件；公开仓库的远程安装仍应在发布后另行实测。
