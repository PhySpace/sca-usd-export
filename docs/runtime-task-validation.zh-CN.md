# 有限原生运行：诊断、任务条件与父进程退出状态

本工具读取已经导出的 USD/report，不构建链、不补写关键配置，不修改材料、求解设置或源层。运行时状态写入内存 session layer；所有输出必须使用新的目录。依赖外部 Isaac Sim，插件没有自带仿真器。包保存、USD 静态验证、有限原生运行、任务行为和理论/实物精度分别记录。

## 调用与任务条件

使用配套 add-on Python 运行外层入口，显式选择 Isaac Python：

```powershell
& $UsdPython -I -B "$Payload/scripts/run_cable_runtime.py" `
  --isaac-python 'C:/isaac-sim/python.bat' `
  --usd "$Case/scene.usda" --report "$Case/scene.report.json" --chain-id wire `
  --output "$Case/native-task-new" --duration 5 --max-duration 5 --max-steps 6000 `
  --mode task --guard-action record --task-criteria "$Case/task-criteria.json"
```

本例假设保存的场景为 1200Hz；步长始终读取 USD/report，不能通过命令覆盖。5秒需要6000步；不同Hz须相应声明预算。默认仍是0.25秒请求、0.5秒/2048步预算，超预算在启动 Isaac 前拒绝。外层另有 `--timeout` 墙钟预算，默认600秒，包含启动；它不是仿真时长或精度条件。外层输出根保存 `launcher.json`、stdout/stderr及 `assessment.json`；子进程输出在 `native/`，包括注册、summary、逐步metrics、NPZ、原生质量/惯量 census及逐项guard事件。Isaac子进程使用 `-s -B`，保留官方python.bat设置的供应方模块路径；`-I`会屏蔽该宿主安装所需的PYTHONPATH。启动前清除父进程PYTHONPATH/PYTHONHOME/PYTHONEXE，再由指定宿主批处理设置自身环境；不向插件runtime借包。

任务JSON使用schema_version `1.0`。`task_id`、`basis`、`required_duration_s`必填；其余判据可省略，但不能关闭完整运行、有限状态、配置/源完整性前提：

```json
{
  "schema_version": "1.0",
  "task_id": "gravity-bending-demonstration",
  "basis": "在运行前说明每个数值的依据和适用边界；不是材料标定或通用精度合同",
  "required_duration_s": 5,
  "max_joint_gap_m": 0.0005,
  "max_endpoint_gap_m": 0.0005,
  "max_free_extension_fraction": 0.05,
  "tip_bending": {
    "axis": "Z", "direction": -1, "window_s": 1,
    "minimum_mean_displacement_m": 0.000001
  }
}
```

示例中的0.5mm/5%是既有工程连接观察值，1µm只示范方向信号的分辨率下限，不能直接照抄为其他任务的材料标准。调用者必须在测试前给出自己的适用依据。未知字段、非有限值、布尔冒充数字、方向非±1、超出观察时长的窗口均拒绝。

`tip_bending`取最后一个自由材料段在free_end的端点，相对保存初态的位移，沿指定轴乘direction；末window_s内样本均值必须**严格大于**minimum_mean_displacement_m。这不是理论挠度、实物测量或静态平衡条件。自由有效长度为自由段初始弦长总和加相邻材料端点的间隙和；增幅以该初始弦长总和归一化。它不包含胶囊端帽，也不表示已实现有限抗拉刚度。

## 四种结果不能混为一谈

1. 运行异常：缺后端、加载失败、无效状态、NaN/Inf、四元数一致性失败、提前关闭等。停止并记录runtime_exception及已有前缀；无法产生summary或清理失败也不能通过。
2. 配置一致性：USD/report声明、Hz、body/joint身份、保存的代理质量/惯量与原生回读、输入和已加载源层SHA。失败单列configuration_consistency，不称作实物材料误差。
3. 工程警戒：现有最大joint gap 0.5mm、线速度10m/s及完整事件仍记录。10m/s是助手设置并沿用的工程停测线，未证明为跳绳或其他对象的物理速度上界；它不是角速度或几何表面点速度。
4. 任务条件：只评估预先提交的JSON。连接gap/长度是否满足本次工程目标、是否发生预期方向的可分辨弯曲，与理论精度、充分静止或实物标定分别说明。

原LIMITS数值不变：joint_gap_m=0.0005，quaternion_norm_error=1e-4，mass_relative_error=1e-4，inertia_relative_error=1e-4，speed_m_s=10。质量/惯量1e-4是保存值与原生代理回读的一致性容差；四元数1e-4是姿态数据一致性检查，不是材料精度。

默认 `--mode legacy --guard-action stop` 保持原工程守卫的停止行为。`passed`继续表示旧守卫及源完整性通过，**不改成新的任务含义**。显式task/record模式遇到gap/speed警戒后记录并继续至固定预算；速度越10仍使旧passed=false，但不独自把task_acceptance判失败。任务JSON若包含gap目标，gap越线仍使对应任务条件false。质量/惯量配置不符、无效姿态和非有限状态仍停止，不为完成5秒隐去失败。

`engineering_guards`保存事件，`runtime_complete`和`integration_completed`区分完整请求及错误，`task_acceptance`保存各项条件和实测值，`physical_accuracy`与`static_equilibrium`保持not_assessed。有限状态及守卫通过不能认证物理准确。

## 父进程与退出码

Isaac的app.close可能在Python正常return之前退出，因此不能用child exit0代替检查通过。外层保存实际PID/命令、输入/脚本SHA和原始退出码，读取注册、census、metrics、NPZ和summary；重新检查真实步数、数组身份/形状/有限性/四元数、源文件、原生质量惯量，并从NPZ重算gap、自由有效长度和tip响应，再独立评估任务条件。missing/malformed证据不能通过。父进程不通过重跑或调整物理参数消除差异。

父进程的metrics重算一致性容差为 `max(1e-12m, 8*float32_eps*world_scale_m)`，world_scale取回读位置绝对值和弦长尺度；长度增幅按自由区间数及参考长度传播此舍入预算。它只覆盖float32及序列化frame的重算舍入，不改变任何运行guard或任务JSON阈值。任务布尔须与从原始NPZ独立算出的结果相同。

- exit0：证据完整，task模式任务条件通过；允许明确记录的工程警告。legacy模式则要求旧passed=true。
- exit1：任务条件失败，或旧工程守卫停止/失败；不是仅凭该码宣布PhysX或实物模型错误。
- exit2：输入/预算拒绝、运行异常、配置不一致、清理失败、证据缺失/不完整/矛盾，或与成功报告冲突的child非零退出。

应同时查看assessment.json的status、逐项checks、task_acceptance和原始native/summary.json。旧passed=false与新task_acceptance=true可以同时出现，例如只越过没有任务适用依据的10m/s警戒；历史守卫结果没有被改成通过。

安装路径经Windows batch启动时不能含双引号、百分号、感叹号或换行，编码token上限为6000字符。模型/报告/配置数据参数通过JSON/Base64传入Isaac Python，不作为cmd片段拼接。任何新参数策略、动作、接触需求或精度目标都需要另立有依据的任务条件；本入口没有新增物理solver。
