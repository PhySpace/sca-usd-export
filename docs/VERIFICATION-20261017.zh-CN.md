# 2026-10-17 本地物理扩展检查

实际使用独立环境 `E:\User\Desktop\sca-addon-state\runtimes\sca-usd-export\.venv\Scripts\python.exe`，Python 3.12.15、SimpleCADAPI 2.2.5、usd-core 26.8、NumPy 2.3.1、cadquery-ocp 7.9.3.1。`check` 验证虚拟环境未继承系统包，所有插件/SDK/原生模块均来自该独立环境。

运行记录为 `E:\User\Desktop\sca-physics\verification\usd-runtime-check.json` 和 `usd-pytest-filter.log`。最终全部 99 项测试通过（22.79 秒），包含真实仿真发现的静态关节端碰撞过滤问题的两项回归。先前两轮 97 项测试也通过，随后仿真发现仅 joint collisionEnabled=false 无法过滤静态端，故增加 leaf collider 显式过滤及验证。测试由真实 SCA 定义、连接器、旋转约束与产品包驱动，原有几何/历史/传输/隔离环境检查也通过。完成实际测试后兼容范围定为 `>=2.2.5,<2.2.6`；未宣称其他 SCA 版本已测。

新增检查覆盖：两个原生密度不同的零件分别绑定接触材料；明确的 0 与 false；不提供外部物理配置 JSON 的最终包独立导出；角阻尼 SI 系数乘 pi/180；移动阻尼 SI 系数不变；显式向上轴覆盖前后记录；无扩展旧包兼容来源；嵌入配置优先且禁止隐式物理覆盖。

15 类 USD 改坏反例全部被检测：静摩擦、动摩擦、恢复系数、阻尼、碰撞开关、碰撞近似、向上方向、重力方向、关节连接对象、锚点、轴、局部旋转、关节两端碰撞、碰撞排除、主动目标速度。对应 `.usda` 及原期望报告留在 `E:\User\Desktop\sca-physics\verification\usd-pytest-work\test_tampered_physics_is_detec*`；不将它们当成有效交付资产。

最终真实摆臂资产另执行 10 项持久改坏反例，全部明确拒绝，产物在 `E:\User\Desktop\sca-physics\verification\tamper-cases`，修改前后与拒绝原因见 `tamper-results.json`（位于其上级 verification）。其中包含删除关节两端实际碰撞形状过滤的反例。复现工具为 `scripts/verify_physics_tamper.py`。有效 `final.usda` 来自只含 `final.scadpkg` 的 `verification/package-only` 目录，在该目录运行导出且未提供任何配置 JSON，证明物理配置可独立随包使用。初次仿真失败的 USD/报告/历史及旧反例保留在 `verification/pre-joint-filter`；没有移动几何或改变静动态分组来绕过问题。

静态校验结果与 Isaac 仿真结果分开。真实 Isaac Sim 5.0 的独立单位标定已经验证：I=0.4 kg*m²、初速度 1 rad/s、阻尼输入 0.4 N*m*s/rad，USD 系数 0.006981317，1 秒实测角度 0.630690 rad，理论 0.632121 rad；证据为 `E:\User\Desktop\sca-physics\verification\angular-unit-calibration.json`。最终摆臂资产的仿真结果另见新插件的验收报告，USD 自身报告不会因这项独立实验自动改写 `simulator_validated`。
